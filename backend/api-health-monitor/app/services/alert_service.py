import asyncio
import logging
import smtplib
import ssl
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, aliased

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import AlertDelivery, CheckResult, Endpoint
from app.services.monitor_targets import pinned_request, resolve_public_addresses

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


def save_check_results(db: Session, results: list[CheckResult]) -> int:
    channels = enabled_channels()
    if not channels:
        db.add_all(results)
        return len(results)

    # Lock endpoint rows in a consistent order on PostgreSQL. SQLite
    # serializes writes when the first result is flushed.
    saved_count = 0
    for result in sorted(results, key=lambda item: item.endpoint_id):
        endpoint = db.scalar(
            select(Endpoint).where(Endpoint.id == result.endpoint_id).with_for_update()
        )
        if endpoint is None:
            continue
        db.add(result)
        db.flush()
        saved_count += 1
        if endpoint.owner_id is not None:
            continue

        latest = db.scalars(
            select(CheckResult)
            .where(CheckResult.endpoint_id == result.endpoint_id)
            .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
            .limit(2)
        ).all()
        if latest[0].id != result.id:
            continue
        if len(latest) == 1:
            if result.is_success:
                continue
        elif latest[1].is_success == result.is_success:
            continue

        payload = {
            "event": "endpoint.recovered" if result.is_success else "endpoint.down",
            "endpoint_id": endpoint.id,
            "endpoint_name": endpoint.name,
            "check_id": result.id,
            "checked_at": result.checked_at.isoformat(),
            "status_code": result.status_code,
        }
        db.add_all(
            AlertDelivery(
                endpoint_id=endpoint.id,
                channel=channel,
                payload=payload,
                next_attempt_at=datetime.now(timezone.utc),
            )
            for channel in channels
        )
    return saved_count


def _send_email(payload: dict, delivery_id: int) -> None:
    status = "recovered" if payload["event"] == "endpoint.recovered" else "down"
    name = " ".join(payload["endpoint_name"].split())
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = settings.ALERT_EMAIL_TO
    message["Subject"] = f"{name} is {status}"
    message.set_content(
        f"{name} is {status}.\n"
        f"Check time: {payload['checked_at']}\n"
        f"HTTP status: {payload['status_code'] or 'unavailable'}\n"
        f"Alert ID: {delivery_id}\n"
    )
    context = ssl.create_default_context()
    if settings.SMTP_SECURITY == "tls":
        connection = smtplib.SMTP_SSL(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=DELIVERY_TIMEOUT, context=context,
        )
    else:
        connection = smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=DELIVERY_TIMEOUT,
        )
    with connection as smtp:
        if settings.SMTP_SECURITY == "starttls":
            smtp.starttls(context=context)
        if settings.SMTP_USERNAME and settings.SMTP_PASSWORD:
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD.get_secret_value())
        smtp.send_message(message)


async def _send_webhook(payload: dict, delivery_id: int) -> None:
    secret = settings.ALERT_WEBHOOK_URL
    if secret is None:
        return
    url = httpx.URL(secret.get_secret_value())
    async with asyncio.timeout(DELIVERY_TIMEOUT):
        addresses = await resolve_public_addresses(url)
        async with httpx.AsyncClient(
            trust_env=False, limits=httpx.Limits(max_keepalive_connections=0),
        ) as client:
            for index, address in enumerate(addresses):
                request = pinned_request(
                    "POST", url, address, DELIVERY_TIMEOUT,
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


async def deliver_pending(session_factory: Callable[[], Session] = SessionLocal) -> None:
    channels = enabled_channels()
    if not channels:
        return
    older = aliased(AlertDelivery)
    earlier_pending = (
        select(older.id)
        .where(
            older.endpoint_id == AlertDelivery.endpoint_id,
            older.channel == AlertDelivery.channel,
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
                ~earlier_pending,
            )
            .order_by(AlertDelivery.next_attempt_at, AlertDelivery.id)
            .limit(20)
        ).all()

    for delivery in deliveries:
        delivery.attempts += 1
        try:
            if delivery.channel == "email":
                await asyncio.to_thread(_send_email, delivery.payload, delivery.id)
            else:
                await _send_webhook(delivery.payload, delivery.id)
        except Exception as error:
            # Destination URLs and credentials must stay out of application logs.
            logger.warning(
                "Alert %s via %s failed on attempt %s (%s)",
                delivery.id, delivery.channel, delivery.attempts, type(error).__name__,
            )
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.state = "failed"
            else:
                delay = RETRY_DELAY * 2 ** (delivery.attempts - 1)
                delivery.next_attempt_at = datetime.now(timezone.utc) + delay
        else:
            delivery.state = "sent"

        with session_factory() as db:
            stored = db.get(AlertDelivery, delivery.id)
            if stored is not None:
                stored.attempts = delivery.attempts
                stored.state = delivery.state
                stored.next_attempt_at = delivery.next_attempt_at
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
                    db.execute(delete(AlertDelivery).where(
                        AlertDelivery.state != "pending",
                        AlertDelivery.created_at < now - timedelta(days=30),
                    ))
                    db.commit()
        except Exception:
            logger.exception("Could not process alert queue")
        await asyncio.sleep(poll_seconds)
