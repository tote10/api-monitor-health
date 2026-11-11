"""Deliver down and recovery alerts to email and webhook channels."""

import asyncio
import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    CHANNEL_EMAIL,
    CHANNEL_WEBHOOK,
    CheckResult,
    Endpoint,
    NotificationChannel,
)
from app.services.url_guard import validate_target_url

logger = logging.getLogger(__name__)

WEBHOOK_TIMEOUT_SECONDS = 10


class ChannelError(Exception):
    """Raised when an alert cannot be delivered."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_channel_target(kind: str, target: str) -> str:
    """Validate a channel destination and return it stripped."""
    target = target.strip()

    if kind == CHANNEL_EMAIL:
        local, _, domain = target.partition("@")
        if not local or "." not in domain or " " in target:
            raise ValueError("Enter a valid email address for an email channel.")
        return target

    if kind == CHANNEL_WEBHOOK:
        validate_target_url(target)
        return target

    raise ValueError(f"Unsupported channel type: {kind}")


def format_transition(
    endpoint: Endpoint, result: CheckResult
) -> tuple[str, str]:
    """Build a subject and plain-text body for one state change."""
    checked_at = _as_utc(result.checked_at) or _utcnow()

    if result.is_success:
        subject = f"RECOVERED: {endpoint.name} is back up"
        lines = [
            f"{endpoint.name} ({endpoint.url}) is responding again.",
            f"Status code: {result.status_code}",
            f"Response time: {result.response_time_ms} ms",
        ]
    else:
        subject = f"DOWN: {endpoint.name} is failing"
        lines = [
            f"{endpoint.name} ({endpoint.url}) failed its health check.",
            f"Status code: {result.status_code or 'no response'}",
            f"Response time: {result.response_time_ms} ms",
            f"Error: {result.error or 'unknown error'}",
        ]

    lines.append(f"Checked at: {checked_at.isoformat()}")
    return subject, "\n".join(lines)


def build_webhook_payload(
    endpoint: Endpoint, result: CheckResult
) -> dict:
    """Build a JSON payload understood by Slack, Discord and generic tools."""
    subject, message = format_transition(endpoint, result)

    return {
        "event": "endpoint_recovered" if result.is_success else "endpoint_down",
        "text": f"{subject}\n{message}",
        "content": f"{subject}\n{message}",
        "endpoint": {
            "id": endpoint.id,
            "name": endpoint.name,
            "url": endpoint.url,
            "status_code": result.status_code,
            "response_time_ms": result.response_time_ms,
            "error": result.error,
        },
        "checked_at": (
            _as_utc(result.checked_at) or _utcnow()
        ).isoformat(),
    }


def should_notify(
    endpoint: Endpoint,
    result: CheckResult,
    previous_success: bool | None,
) -> bool:
    """Only alert on a state change, and rate-limit repeat failures."""
    if not endpoint.alerts_enabled:
        return False

    if previous_success == result.is_success:
        return False

    # The first check of a healthy endpoint is not news.
    if previous_success is None and result.is_success:
        return False

    if not result.is_success and settings.ALERT_COOLDOWN_MINUTES > 0:
        last_alert = _as_utc(endpoint.last_alert_at)
        if last_alert is not None and _utcnow() - last_alert < timedelta(
            minutes=settings.ALERT_COOLDOWN_MINUTES
        ):
            return False

    return True


def list_channels(db: Session) -> list[NotificationChannel]:
    return list(
        db.scalars(select(NotificationChannel).order_by(NotificationChannel.id))
        .all()
    )


def enabled_channels(db: Session) -> list[NotificationChannel]:
    return list(
        db.scalars(
            select(NotificationChannel)
            .where(NotificationChannel.is_enabled.is_(True))
            .order_by(NotificationChannel.id)
        ).all()
    )


def create_webhook_client() -> httpx.AsyncClient:
    """Create the HTTP client used for webhooks (overridable in tests)."""
    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=WEBHOOK_TIMEOUT_SECONDS,
    )


async def _send_webhook(target: str, payload: dict) -> None:
    # Resolution blocks; keep it off the event loop like the check path does.
    await asyncio.to_thread(validate_target_url, target)

    async with create_webhook_client() as client:
        response = await client.post(target, json=payload)

    if response.status_code >= 400:
        raise ChannelError(
            f"Webhook returned HTTP {response.status_code}."
        )


def _send_email(target: str, subject: str, message: str) -> None:
    if not settings.SMTP_HOST:
        raise ChannelError(
            "SMTP is not configured. Set SMTP_HOST to send email alerts."
        )

    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = settings.SMTP_FROM
    email["To"] = target
    email.set_content(message)

    with smtplib.SMTP(
        settings.SMTP_HOST, settings.SMTP_PORT, timeout=10
    ) as smtp:
        if settings.SMTP_USE_TLS:
            smtp.starttls()
        if settings.SMTP_USERNAME:
            smtp.login(
                settings.SMTP_USERNAME, settings.SMTP_PASSWORD or ""
            )
        smtp.send_message(email)


async def deliver(
    db: Session,
    channel: NotificationChannel,
    subject: str,
    message: str,
    payload: dict,
) -> bool:
    """Send one alert through one channel and record the outcome."""
    try:
        if channel.kind == CHANNEL_EMAIL:
            await asyncio.to_thread(
                _send_email, channel.target, subject, message
            )
        elif channel.kind == CHANNEL_WEBHOOK:
            await _send_webhook(channel.target, payload)
        else:
            raise ChannelError(
                f"Unsupported channel type: {channel.kind}"
            )
    except Exception as exc:  # Delivery problems must never fail a check.
        channel.last_error = f"{type(exc).__name__}: {exc}"[:500]
        db.commit()
        logger.warning(
            "Alert delivery failed for channel %s (%s): %s",
            channel.id,
            channel.name,
            exc,
        )
        return False

    channel.last_sent_at = _utcnow()
    channel.last_error = None
    db.commit()
    return True


async def notify_state_change(
    db: Session,
    endpoint: Endpoint,
    result: CheckResult,
    previous_success: bool | None,
) -> int:
    """Alert every enabled channel when an endpoint goes down or recovers."""
    if not should_notify(endpoint, result, previous_success):
        return 0

    channels = enabled_channels(db)
    if not channels:
        return 0

    subject, message = format_transition(endpoint, result)
    payload = build_webhook_payload(endpoint, result)

    delivered = 0
    for channel in channels:
        if await deliver(db, channel, subject, message, payload):
            delivered += 1

    endpoint.last_alert_at = _utcnow()
    db.commit()
    return delivered


async def send_test_alert(
    db: Session, channel: NotificationChannel
) -> tuple[bool, str | None]:
    subject = "Test alert from API Health Monitor"
    message = (
        "This is a test notification. Alerts will arrive here when a "
        "monitored endpoint goes down or recovers."
    )
    payload = {
        "event": "test",
        "text": f"{subject}\n{message}",
        "content": f"{subject}\n{message}",
    }

    sent = await deliver(db, channel, subject, message, payload)
    return sent, channel.last_error
