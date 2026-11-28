import asyncio
import logging
import secrets
import smtplib
import ssl
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import AlertAttempt, AlertDelivery, AlertIntegration, CheckResult, Endpoint, Incident, MaintenanceWindow
from app.services.email_content import build_message, public_failure_reason
from app.services.email_notifications import email_destination, queue_incident_emails, queue_weekly_digests
from app.services.incident_service import transition_incident
from app.services.monitor_targets import pinned_request, resolve_public_addresses
from app.services.request_config import monitor_cipher
from app.services.routing import cancel_escalations, has_delivery, queue_integrations

logger = logging.getLogger(__name__)
RETRY_DELAY = timedelta(seconds=30)
MAX_ATTEMPTS = 5
DELIVERY_TIMEOUT = 10


def enabled_channels() -> tuple[str, ...]:
    channels = []
    if settings.ALERT_WEBHOOK_URL:
        channels.append("webhook")
    if settings.SMTP_HOST:
        channels.append("email")
    return tuple(channels)


def in_maintenance(db: Session, endpoint_id: int, at: datetime) -> bool:
    return db.scalar(select(MaintenanceWindow.id).where(
        MaintenanceWindow.endpoint_id == endpoint_id, MaintenanceWindow.starts_at <= at,
        MaintenanceWindow.ends_at > at,
        or_(MaintenanceWindow.canceled_at.is_(None), MaintenanceWindow.canceled_at > at),
    ).limit(1)) is not None


def save_check_results(db: Session, results: list[CheckResult], *, demo_base: str | None = None) -> int:
    channels = enabled_channels() if demo_base is None else ()
    # Global email is a legacy shared-workspace destination.
    if not settings.ALERT_EMAIL_TO:
        channels = tuple(channel for channel in channels if channel != "email")
    saved_count = 0
    for result in sorted(results, key=lambda item: (item.endpoint_id, item.checked_at)):
        endpoint = db.scalar(
            select(Endpoint).where(Endpoint.id == result.endpoint_id).with_for_update()
        )
        if endpoint is None:
            continue
        db.add(result)
        db.flush()
        saved_count += 1
        incident = transition_incident(db, endpoint, result)
        expiry = result.certificate_expires_at
        tls_warning = bool(
            expiry and settings.TLS_EXPIRY_WARNING_DAYS
            and expiry - result.checked_at <= timedelta(days=settings.TLS_EXPIRY_WARNING_DAYS)
        )
        if incident is None and not tls_warning:
            continue
        if incident is not None and result.is_success:
            cancel_escalations(db, incident.id)
        if in_maintenance(db, endpoint.id, result.checked_at):
            continue

        if tls_warning:
            queue_tls_warning(db, endpoint, result, channels)
        if incident is None:
            continue
        payload = {
            "event": "endpoint.recovered" if result.is_success else "endpoint.down",
            "endpoint_id": endpoint.id,
            "endpoint_name": endpoint.name,
            "severity": endpoint.severity,
            "check_id": result.id,
            "incident_id": incident.id,
            "failure_reason": incident.first_error,
            "checked_at": result.checked_at.isoformat(),
            "status_code": result.status_code,
        }
        if settings.PUBLIC_BASE_URL:
            payload["incident_url"] = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/?endpoint={endpoint.id}&incident={incident.id}"
        now = datetime.now(timezone.utc)
        if demo_base is None:
            queue_integrations(db, endpoint, incident, payload, now)
        managed_addresses = queue_incident_emails(db, endpoint, incident, result, now, links_base=demo_base)
        email_payload = {
            "event": payload["event"], "endpoint_name": endpoint.name,
            "checked_at": payload["checked_at"], "detected_at": incident.opened_at.isoformat(),
            "incident_url": payload.get("incident_url"),
        }
        if result.is_success:
            email_payload.update(
                resolved_at=incident.recovered_at.isoformat(),
                duration_seconds=round((incident.recovered_at - incident.first_failed_at).total_seconds()),
            )
        else:
            email_payload["safe_reason"] = public_failure_reason(result.failure_kind, result.status_code)
        db.add_all(
            AlertDelivery(
                endpoint_id=endpoint.id,
                channel=channel,
                incident_id=incident.id,
                dedup_key=f"incident:{incident.id}:{channel}:{payload['event']}",
                payload=email_payload if channel == "email" else payload,
                next_attempt_at=now,
            )
            for channel in channels if endpoint.owner_id is None
            and (channel != "email" or settings.ALERT_EMAIL_TO.strip().lower() not in managed_addresses)
        )
    return saved_count


def queue_tls_warning(db: Session, endpoint: Endpoint, result: CheckResult, channels: tuple[str, ...]):
    expiry = result.certificate_expires_at.isoformat()
    now = datetime.now(timezone.utc)
    payload = {
        "event": "endpoint.certificate_expiring", "endpoint_id": endpoint.id,
        "endpoint_name": endpoint.name, "certificate_expires_at": expiry,
        "checked_at": result.checked_at.isoformat(), "status_code": result.status_code,
        "severity": endpoint.severity, "failure_reason": "TLS certificate expires " + expiry,
    }
    queue_integrations(db, endpoint, None, payload, now)
    if endpoint.owner_id is None:
        for channel in channels:
            key = f"tls:{endpoint.id}:{channel}:{expiry}"
            if not has_delivery(db, key):
                db.add(AlertDelivery(endpoint_id=endpoint.id, channel=channel, payload=payload,
                                     next_attempt_at=now, dedup_key=key))


def _send_email(payload: dict, delivery_id: int, recipient: str | None = None) -> None:
    recipient = recipient or settings.ALERT_EMAIL_TO
    host = settings.SMTP_FROM.rsplit("@", 1)[-1].rstrip(">")
    instance = sha256((settings.PUBLIC_BASE_URL or settings.SMTP_FROM).encode()).hexdigest()[:16]
    message = build_message(payload, recipient, settings.SMTP_FROM, f"<monitor-{instance}-{delivery_id}@{host}>")
    context = ssl.create_default_context()
    if settings.SMTP_SECURITY == "tls":
        connection = smtplib.SMTP_SSL(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=DELIVERY_TIMEOUT, context=context,
        )
    else:
        connection = smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=DELIVERY_TIMEOUT,
        )
    accepted = False
    try:
        with connection as smtp:
            if settings.SMTP_SECURITY == "starttls":
                smtp.starttls(context=context)
            if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
                smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD.get_secret_value())
            refusals = smtp.send_message(message)
            if refusals:
                raise smtplib.SMTPRecipientsRefused(refusals)
            accepted = True
    except (OSError, smtplib.SMTPException):
        # A failed QUIT cannot undo a successful DATA response.
        if not accepted:
            raise


def delivery_error(error: Exception) -> tuple[str, bool]:
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in error.recipients.values()]
        if codes:
            return f"SMTP {codes[0]}", all(500 <= code < 600 for code in codes)
    if isinstance(error, smtplib.SMTPResponseException):
        return f"SMTP {error.smtp_code}", 500 <= error.smtp_code < 600
    return type(error).__name__, False


async def _send_webhook(payload: dict, delivery_id: int) -> None:
    secret = settings.ALERT_WEBHOOK_URL
    if secret is None:
        return
    await send_webhook(secret.get_secret_value(), payload, delivery_id)


async def send_webhook(destination: str, payload: dict, delivery_id: int) -> None:
    url = httpx.URL(destination)
    async with asyncio.timeout(DELIVERY_TIMEOUT):
        addresses = await resolve_public_addresses(url)
        async with httpx.AsyncClient(
            trust_env=False, limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            for index, address in enumerate(addresses):
                request = pinned_request(
                    "POST", url, address, DELIVERY_TIMEOUT,
                    headers={"Idempotency-Key": f"monitor-delivery-{delivery_id}"},
                    json={**payload, "delivery_id": delivery_id},
                )
                try:
                    response = await client.send(request, stream=True, follow_redirects=False)
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if index == len(addresses) - 1:
                        raise
                    continue
                try:
                    response.raise_for_status()
                    return
                finally:
                    await response.aclose()


async def deliver_pending(
    session_factory: Callable[[], Session] = SessionLocal, *, capture: Callable | None = None,
) -> None:
    channels = ("email",) if capture is not None else (*enabled_channels(), "integration")
    older = aliased(AlertDelivery)
    earlier_pending = (
        select(older.id)
        .where(
            older.endpoint_id.is_not_distinct_from(AlertDelivery.endpoint_id),
            older.channel == AlertDelivery.channel,
            older.integration_id.is_not_distinct_from(AlertDelivery.integration_id),
            older.recipient_id.is_not_distinct_from(AlertDelivery.recipient_id),
            older.state == "pending",
            older.id < AlertDelivery.id,
        )
        .exists()
    )
    with session_factory() as db:
        deliveries = db.scalars(
            select(AlertDelivery)
            .where(
                AlertDelivery.state == "pending",
                AlertDelivery.channel.in_(channels),
                AlertDelivery.next_attempt_at <= datetime.now(timezone.utc),
                or_(AlertDelivery.delivery_lease_until.is_(None),
                    AlertDelivery.delivery_lease_until <= datetime.now(timezone.utc)),
                ~earlier_pending,
            )
            .order_by(AlertDelivery.next_attempt_at, AlertDelivery.id)
            .limit(20)
        ).all()

    for delivery in deliveries:
        token = secrets.token_hex(16)
        now = datetime.now(timezone.utc)
        with session_factory() as db:
            claimed = db.execute(update(AlertDelivery).where(
                AlertDelivery.id == delivery.id, AlertDelivery.state == "pending",
                AlertDelivery.attempts == delivery.attempts, AlertDelivery.next_attempt_at <= now,
                or_(AlertDelivery.delivery_lease_until.is_(None), AlertDelivery.delivery_lease_until <= now),
            ).values(
                delivery_token=token, delivery_lease_until=now + timedelta(seconds=120),
                attempts=AlertDelivery.attempts + 1, last_attempt_at=now,
            )).rowcount
            if claimed:
                db.execute(update(AlertAttempt).where(
                    AlertAttempt.delivery_id == delivery.id, AlertAttempt.outcome == "in_progress",
                ).values(outcome="unknown", finished_at=now))
                if delivery.attempts < MAX_ATTEMPTS:
                    db.add(AlertAttempt(delivery_id=delivery.id, number=delivery.attempts + 1, started_at=now))
            db.commit()
        if not claimed:
            continue
        delivery.attempts += 1
        destination = None
        recipient = None
        with session_factory() as db:
            event = delivery.payload["event"]
            skip_delivery = (
                delivery.incident_id is not None and in_maintenance(db, delivery.endpoint_id, now)
            ) or (
                event == "endpoint.certificate_expiring" and in_maintenance(db, delivery.endpoint_id, now)
            )
            if delivery.channel == "email":
                if delivery.recipient_id is not None:
                    recipient = email_destination(db, delivery, now)
                elif capture is None:
                    recipient = settings.ALERT_EMAIL_TO.strip()
                skip_delivery = skip_delivery or not recipient
            if delivery.channel == "integration":
                route = db.get(AlertIntegration, delivery.integration_id)
                endpoint = db.get(Endpoint, delivery.endpoint_id)
                if route and route.enabled and endpoint and route.owner_id == endpoint.owner_id:
                    destination = (route.kind, route.destination)
                else:
                    skip_delivery = True
                if event == "endpoint.escalated":
                    incident = db.get(Incident, delivery.incident_id)
                    if not incident or incident.acknowledged_at or incident.recovered_at:
                        skip_delivery = True
            if skip_delivery:
                db.execute(update(AlertDelivery).where(
                    AlertDelivery.id == delivery.id, AlertDelivery.delivery_token == token,
                ).values(state="canceled", delivery_token=None, delivery_lease_until=None,
                         payload={"event": event} if event == "email.confirm" else delivery.payload))
                attempt = db.get(AlertAttempt, (delivery.id, delivery.attempts))
                if attempt:
                    attempt.outcome = "canceled"
                    attempt.finished_at = now
                db.commit()
                continue
        if delivery.attempts > MAX_ATTEMPTS:
            with session_factory() as db:
                db.execute(update(AlertDelivery).where(
                    AlertDelivery.id == delivery.id, AlertDelivery.delivery_token == token,
                ).values(state="failed", delivery_token=None, delivery_lease_until=None))
                db.commit()
            continue
        last_error = None
        try:
            if destination:
                kind, ciphertext = destination
                address = monitor_cipher().decrypt(ciphertext.encode()).decode()
                payload = delivery.payload
                if kind in {"slack", "discord"}:
                    message = f"{payload['endpoint_name']} · {payload['event']} ({payload['checked_at']})"
                    if payload.get("failure_reason"):
                        message += "\n" + payload["failure_reason"]
                    if payload.get("incident_url"):
                        message += "\n" + payload["incident_url"]
                    payload = ({"text": message, "mrkdwn": False, "unfurl_links": False, "unfurl_media": False}
                               if kind == "slack" else {"content": message, "allowed_mentions": {"parse": []}})
                await send_webhook(address, payload, delivery.id)
            elif delivery.channel == "email":
                if capture is None:
                    await asyncio.to_thread(_send_email, delivery.payload, delivery.id, recipient)
                else:
                    capture(delivery.payload, delivery.id, recipient)
            else:
                await _send_webhook(delivery.payload, delivery.id)
        except Exception as error:
            last_error, permanent = delivery_error(error)
            # Destination URLs and credentials must stay out of application logs.
            logger.warning(
                "Alert %s via %s failed on attempt %s (%s)",
                delivery.id, delivery.channel, delivery.attempts, type(error).__name__,
            )
            if permanent or delivery.attempts >= MAX_ATTEMPTS:
                delivery.state = "failed"
            else:
                delay = RETRY_DELAY * 2 ** (delivery.attempts - 1)
                delivery.next_attempt_at = datetime.now(timezone.utc) + delay
        else:
            delivery.state = "sent"

        finished_at = datetime.now(timezone.utc)
        with session_factory() as db:
            updated = db.execute(update(AlertDelivery).where(
                AlertDelivery.id == delivery.id, AlertDelivery.delivery_token == token,
            ).values(
                state=delivery.state, next_attempt_at=delivery.next_attempt_at, last_error=last_error,
                accepted_at=finished_at if delivery.state == "sent" else None,
                payload={"event": event} if event == "email.confirm" and delivery.state != "pending" else delivery.payload,
                delivery_token=None, delivery_lease_until=None,
            )).rowcount
            if updated:
                attempt = db.get(AlertAttempt, (delivery.id, delivery.attempts))
                attempt.finished_at = finished_at
                attempt.outcome = "failed" if last_error else "accepted"
                attempt.error = last_error
            db.commit()



async def alert_loop(poll_seconds: float) -> None:
    next_cleanup = datetime.min.replace(tzinfo=timezone.utc)
    while True:
        try:
            await deliver_pending()
            now = datetime.now(timezone.utc)
            if now >= next_cleanup:
                next_cleanup = now + timedelta(hours=1)
                with SessionLocal() as db:
                    queue_weekly_digests(db, now)
                    expired = db.scalars(select(AlertDelivery).where(
                        AlertDelivery.state != "pending",
                        AlertDelivery.created_at < now - timedelta(days=30),
                    ))
                    for delivery in expired:
                        db.delete(delivery)
                    db.commit()
        except Exception:
            logger.exception("Could not process alert queue")
        await asyncio.sleep(poll_seconds)
