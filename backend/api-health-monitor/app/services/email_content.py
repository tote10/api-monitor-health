from datetime import datetime, timezone
from email.message import EmailMessage
from functools import lru_cache
from html import escape
from pathlib import Path
import re
from string import Template


SAFE_REASONS = {
    "assertion": "Response did not match the expected result",
    "configuration": "Check configuration could not be used",
    "connectivity": "Could not connect to the service",
    "destination": "Check destination was blocked",
    "missed_heartbeat": "Expected heartbeat did not arrive",
    "timeout": "Check timed out",
}


def service_name(value: str) -> str:
    name = " ".join(value.split())
    return re.sub(r"(?:[a-z][a-z0-9+.-]*://|www\.)\S+", "[address omitted]", name, flags=re.I)


def safe_failure_reason(kind: str | None, status_code: int | None) -> str:
    if kind in (None, "http_status") and isinstance(status_code, int) and 100 <= status_code <= 599:
        return f"Unexpected HTTP status {status_code}"
    return SAFE_REASONS.get(kind, "Availability check failed")


def format_timestamp(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return "Unknown"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M:%S UTC")


def format_duration(seconds: int) -> str:
    minutes, seconds = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return " ".join(part for part in (
        f"{hours}h" if hours else "", f"{minutes}m" if minutes else "", f"{seconds}s",
    ) if part)


@lru_cache(maxsize=1)
def html_template() -> Template:
    path = Path(__file__).resolve().parents[1] / "templates" / "email" / "notification.html"
    return Template(path.read_text(encoding="utf-8"))


def build_message(payload: dict, recipient: str, sender: str, message_id: str) -> EmailMessage:
    event = payload["event"]
    name = service_name(payload.get("endpoint_name", "Service"))
    details = []
    actions = []
    if event == "endpoint.down":
        title = f"{name} is down"
        details.append(f"Detected at {format_timestamp(payload.get('detected_at') or payload['checked_at'])}")
        if not payload.get("public"):
            reason = payload.get("safe_reason") or safe_failure_reason(None, payload.get("status_code"))
            details.append(f"Reason: {reason}")
    elif event == "endpoint.recovered":
        title = f"{name} is recovered"
        details.append(f"Resolved at {format_timestamp(payload.get('resolved_at') or payload['checked_at'])}")
        if payload.get("duration_seconds") is not None:
            details.append(f"Duration: {format_duration(payload['duration_seconds'])}")
    elif event == "endpoint.certificate_expiring":
        title = f"{name} TLS certificate is expiring"
        details.append(f"Certificate expires at {format_timestamp(payload['certificate_expires_at'])}")
    elif event == "email.weekly_digest":
        title = "Weekly service digest"
        details.append(f"{payload['week_start']} through {payload['week_end']} (UTC)")
        for row in payload["services"]:
            uptime = "Unknown" if row["uptime"] is None else f"{row['uptime']:.1f}%"
            details.append(f"{service_name(row['name'])}: {uptime} uptime · {row['incidents']} incidents opened")
    elif event == "email.confirm":
        title = "Confirm your email address"
        if payload.get("public"):
            details.append(f"Confirm to receive status updates for {name}.")
        else:
            details.append("Confirm this address to receive monitoring notifications.")
        details.append("This link expires in 24 hours.")
        actions.append(("Confirm email address", payload["confirm_url"]))
    else:
        title = "Test monitoring email"
        details.append("This address can receive notifications from API Health Monitor.")

    incident_url = payload.get("incident_url")
    if incident_url:
        actions.append(("View service status" if payload.get("public") else "View incident", incident_url))
    if payload.get("dashboard_url"):
        actions.append(("View dashboard", payload["dashboard_url"]))
    if payload.get("unsubscribe_url"):
        actions.append(("Unsubscribe", payload["unsubscribe_url"]))

    text = "\n\n".join([title, *details, *(f"{label}: {url}" for label, url in actions)])
    html = html_template().substitute(
        title=escape(title),
        details="".join(f'<p style="margin:8px 0">{escape(value)}</p>' for value in details),
        actions="".join(
            f'<p style="margin:20px 0 0"><a style="color:#1456a0" href="{escape(url, quote=True)}">'
            f"{escape(label)}</a></p>" for label, url in actions
        ),
    )
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = title
    message["Message-ID"] = message_id
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message
