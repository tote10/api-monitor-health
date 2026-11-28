from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import re
import secrets

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import AlertDelivery, CheckResult, EmailMonitor, EmailRecipient, Endpoint, Incident, StatusPage, User
from app.services.email_content import public_failure_reason, service_name
from app.services.routing import has_delivery


ADDRESS_PATTERN = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)
CONFIRMATION_TTL = timedelta(days=1)
CONFIRMATION_COOLDOWN = timedelta(minutes=10)


def normalize_address(value: str) -> str:
    address = value.strip().lower()
    if len(address) > 254 or len(address.split("@")[0]) > 64 or not ADDRESS_PATTERN.fullmatch(address):
        raise ValueError("Enter a valid email address")
    return address


def token_hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def absolute_url(path: str) -> str:
    return settings.PUBLIC_BASE_URL.rstrip("/") + path


def account_owner(user: User) -> int | None:
    return user.id if user.role == "member" else None


def status_scope(owner_id: int | None) -> str:
    return f"user:{owner_id}" if owner_id is not None else "shared"


def published_page(db: Session, endpoint: Endpoint) -> StatusPage | None:
    if not endpoint.public_label:
        return None
    page = db.get(StatusPage, status_scope(endpoint.owner_id))
    return page if page and page.enabled else None


def email_ready() -> bool:
    return bool(settings.SMTP_HOST and settings.PUBLIC_BASE_URL)


def unsubscribe_token(recipient: EmailRecipient) -> str:
    signature = hmac.digest(recipient.unsubscribe_hash.encode(), str(recipient.id).encode(), "sha256").hex()
    return f"{recipient.id}.{signature}"


def queue_email(db: Session, recipient: EmailRecipient, payload: dict, now: datetime,
                *, endpoint: Endpoint | None = None, incident: Incident | None = None,
                dedup_key: str | None = None) -> AlertDelivery:
    delivery = AlertDelivery(
        endpoint_id=endpoint.id if endpoint else None,
        incident_id=incident.id if incident else None,
        recipient_id=recipient.id, channel="email", payload=payload,
        next_attempt_at=now, dedup_key=dedup_key,
    )
    db.add(delivery)
    return delivery


def queue_confirmation(db: Session, recipient: EmailRecipient, now: datetime,
                       endpoint: Endpoint | None = None) -> bool:
    if recipient.verified_at and not recipient.disabled_at:
        return False
    if recipient.confirmation_expires_at and recipient.confirmation_expires_at > now + CONFIRMATION_TTL - CONFIRMATION_COOLDOWN:
        return False
    token = secrets.token_urlsafe(32)
    recipient.confirmation_hash = token_hash(token)
    recipient.confirmation_expires_at = now + CONFIRMATION_TTL
    recipient.verified_at = None
    recipient.disabled_at = None
    payload = {
        "event": "email.confirm", "confirm_url": absolute_url(f"/email/confirm#{token}"),
        "confirmation_key": recipient.confirmation_hash,
    }
    if endpoint:
        payload.update(public=True, endpoint_name=service_name(endpoint.public_label))
    queue_email(db, recipient, payload, now, endpoint=endpoint)
    return True


def queue_incident_emails(
    db: Session, endpoint: Endpoint, incident: Incident, check: CheckResult, now: datetime,
    *, demo: bool = False,
) -> set[str]:
    if not demo and not email_ready():
        return set()
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    event = "endpoint.recovered" if check.is_success else "endpoint.down"
    payload = {
        "event": event, "checked_at": check.checked_at.isoformat(), "detected_at": incident.opened_at.isoformat(),
    }
    if check.is_success:
        payload.update(
            resolved_at=incident.recovered_at.isoformat(),
            duration_seconds=round((incident.recovered_at - incident.first_failed_at).total_seconds()),
        )
    else:
        payload["safe_reason"] = public_failure_reason(check.failure_kind, check.status_code)

    recipients = db.scalars(select(EmailRecipient).join(EmailMonitor).join(User).where(
        EmailMonitor.endpoint_id == endpoint.id,
        User.role.in_(("admin", "member", "viewer")),
        (User.role == "member") if endpoint.owner_id is not None else (User.role != "member"),
        User.id == endpoint.owner_id if endpoint.owner_id is not None else EmailRecipient.user_id.is_not(None),
        EmailRecipient.verified_at.is_not(None), EmailRecipient.disabled_at.is_(None),
    ).order_by(EmailRecipient.id))
    addresses = set()
    for recipient in recipients:
        queue_incident_email(db, recipient, endpoint, incident, {
            **payload, "endpoint_name": service_name(endpoint.name),
            "incident_url": f"{base}/?endpoint={endpoint.id}&incident={incident.id}" if not demo else "/demo#incident",
        }, now, addresses)

    page = published_page(db, endpoint) if not demo else None
    if page:
        public_recipients = db.scalars(select(EmailRecipient).where(
            EmailRecipient.public_endpoint_id == endpoint.id,
            EmailRecipient.verified_at.is_not(None), EmailRecipient.disabled_at.is_(None),
        ).order_by(EmailRecipient.id))
        public_payload = {key: value for key, value in payload.items() if key != "safe_reason"}
        for recipient in public_recipients:
            queue_incident_email(db, recipient, endpoint, incident, {
                **public_payload, "endpoint_name": service_name(endpoint.public_label), "public": True,
                "incident_url": f"{base}/status/{page.slug}#service-{endpoint.id}",
                "unsubscribe_url": f"{base}/email/unsubscribe#{unsubscribe_token(recipient)}",
            }, now, addresses)
    return addresses


def queue_incident_email(db, recipient, endpoint, incident, payload, now, addresses):
    key = f"email:{incident.id}:{payload['event']}:{token_hash(recipient.address)}"
    if recipient.address not in addresses and not has_delivery(db, key):
        queue_email(db, recipient, payload, now, endpoint=endpoint, incident=incident, dedup_key=key)
    addresses.add(recipient.address)


def email_destination(db: Session, delivery: AlertDelivery, now: datetime) -> str | None:
    recipient = db.get(EmailRecipient, delivery.recipient_id)
    if not recipient or recipient.disabled_at:
        return None
    endpoint = db.get(Endpoint, delivery.endpoint_id) if delivery.endpoint_id else None
    if recipient.public_endpoint_id is not None:
        if not endpoint or recipient.public_endpoint_id != endpoint.id or not published_page(db, endpoint):
            return None
    event = delivery.payload["event"]
    if event == "email.confirm":
        valid = (
            recipient.confirmation_hash == delivery.payload.get("confirmation_key")
            and recipient.confirmation_expires_at and recipient.confirmation_expires_at > now
        )
        return recipient.address if valid else None
    if not recipient.verified_at:
        return None
    if recipient.public_endpoint_id is None:
        user = db.get(User, recipient.user_id)
        if not user or (endpoint and (
            endpoint.owner_id != account_owner(user) or not db.get(EmailMonitor, (recipient.id, endpoint.id))
        )):
            return None
    if event == "email.weekly_digest":
        if not recipient.weekly_digest:
            return None
        # Recheck the selected monitor set before releasing a queued report.
        selected = set(db.scalars(select(EmailMonitor.endpoint_id).join(Endpoint).where(
            EmailMonitor.recipient_id == recipient.id, Endpoint.owner_id == account_owner(user),
        )))
        if not set(delivery.payload["monitor_ids"]).issubset(selected):
            return None
    return recipient.address


def queue_weekly_digests(db: Session, now: datetime) -> None:
    if not email_ready():
        return
    end = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    recipients = db.execute(select(EmailRecipient, User).join(User).where(
        EmailRecipient.weekly_digest.is_(True), EmailRecipient.verified_at < end,
        EmailRecipient.disabled_at.is_(None),
    ).order_by(EmailRecipient.id))
    date_range = (CheckResult.checked_at >= start, CheckResult.checked_at < end)
    totals = select(func.count(CheckResult.id)).where(
        CheckResult.endpoint_id == Endpoint.id, *date_range,
        or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured"),
    ).correlate(Endpoint)
    opened = select(func.count(Incident.id)).where(
        Incident.endpoint_id == Endpoint.id, Incident.opened_at >= start, Incident.opened_at < end,
    ).correlate(Endpoint)
    for recipient, user in recipients:
        key = f"digest:{recipient.id}:{start.date()}"
        if has_delivery(db, key):
            continue
        monitors = db.execute(select(
            Endpoint.id, Endpoint.name, totals.scalar_subquery(),
            totals.where(CheckResult.is_success.is_(True)).scalar_subquery(), opened.scalar_subquery(),
        ).join(EmailMonitor).where(
            EmailMonitor.recipient_id == recipient.id, Endpoint.owner_id == account_owner(user),
        ).order_by(Endpoint.id)).all()
        if not monitors:
            continue
        try:
            with db.begin_nested():
                queue_email(db, recipient, {
                    "event": "email.weekly_digest", "week_start": str(start.date()),
                    "week_end": str((end - timedelta(days=1)).date()),
                    "dashboard_url": absolute_url("/"),
                    "monitor_ids": [item[0] for item in monitors],
                    "services": [{
                        "name": service_name(name), "uptime": 100 * up / total if total else None,
                        "incidents": count,
                    } for _, name, total, up, count in monitors],
                }, now, dedup_key=key)
        except IntegrityError:
            if not has_delivery(db, key):
                raise
