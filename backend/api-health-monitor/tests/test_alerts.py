import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import settings
from app.db.models import AlertDelivery, CheckResult, Endpoint
from app.services import alert_service, monitor_service


def test_manual_and_scheduled_checks_queue_only_transitions(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", SecretStr("https://example.com/alerts"))
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.example.com")
    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", "ops@example.com")
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    observations = iter([
        (True, start),
        (False, start + timedelta(seconds=10)),
        (False, start + timedelta(seconds=20)),
        (True, start + timedelta(seconds=30)),
    ])

    async def check(endpoint, client):
        success, checked_at = next(observations)
        return CheckResult(
            endpoint_id=endpoint.id,
            is_success=success,
            checked_at=checked_at,
        )

    monkeypatch.setattr(monitor_service, "check_endpoint", check)
    endpoint = client.post("/api/v1/endpoints", json={
        "name": "Test", "url": "https://example.com/", "interval_seconds": 10,
    }).json()
    path = f"/api/v1/endpoints/{endpoint['id']}"
    for _ in range(3):
        assert client.post(path + "/check").status_code == 201
    assert asyncio.run(monitor_service.run_due_checks(session_factory)) == 1

    with session_factory() as db:
        deliveries = db.scalars(select(AlertDelivery).order_by(AlertDelivery.id)).all()
        assert [(item.channel, item.payload["event"]) for item in deliveries] == [
            ("webhook", "endpoint.down"),
            ("email", "endpoint.down"),
            ("webhook", "endpoint.recovered"),
            ("email", "endpoint.recovered"),
        ]
    assert client.delete(path).status_code == 204
    with session_factory() as db:
        assert db.scalars(select(AlertDelivery)).all() == []


def test_delivery_retries_and_keeps_events_in_order(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", SecretStr("https://example.com/alerts"))
    send = AsyncMock(side_effect=[httpx.ConnectError("temporary error"), None, None])
    monkeypatch.setattr(alert_service, "_send_webhook", send)
    with session_factory() as db:
        endpoint = Endpoint(name="Test", url="https://example.com/")
        db.add(endpoint)
        db.flush()
        for event in ("endpoint.down", "endpoint.recovered"):
            db.add(AlertDelivery(
                endpoint_id=endpoint.id,
                channel="webhook",
                payload={"event": event},
                next_attempt_at=datetime.now(timezone.utc),
            ))
        db.commit()

    asyncio.run(alert_service.deliver_pending(session_factory))
    with session_factory() as db:
        down, recovered = db.scalars(select(AlertDelivery).order_by(AlertDelivery.id)).all()
        assert down.state == recovered.state == "pending"
        assert down.attempts == 1
        assert recovered.attempts == 0
        assert down.next_attempt_at > datetime.now(timezone.utc)
        down.next_attempt_at = datetime.now(timezone.utc)
        db.commit()

    for _ in range(2):
        asyncio.run(alert_service.deliver_pending(session_factory))
    assert [call.args[0]["event"] for call in send.await_args_list] == [
        "endpoint.down", "endpoint.down", "endpoint.recovered",
    ]
    with session_factory() as db:
        assert db.scalars(select(AlertDelivery.state)).all() == ["sent", "sent"]


def test_smtp_delivery_uses_configured_recipient_and_starttls(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "mail.example.com")
    monkeypatch.setattr(settings, "SMTP_SECURITY", "starttls")
    monkeypatch.setattr(settings, "SMTP_FROM", "monitor@example.com")
    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", "ops@example.com")
    smtp = Mock()
    smtp.send_message.return_value = {}
    connection = Mock()
    connection.__enter__ = Mock(return_value=smtp)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(alert_service.smtplib, "SMTP", Mock(return_value=connection))

    alert_service._send_email({
        "event": "endpoint.down", "endpoint_name": "Demo", "checked_at": "2026-11-26T00:00:00Z",
        "status_code": 503,
    }, 42)

    smtp.starttls.assert_called_once()
    message = smtp.send_message.call_args.args[0]
    assert message["To"] == "ops@example.com"
    assert message["Subject"] == "Demo is down"
    assert "503" in message.get_body(preferencelist=("plain",)).get_content()


def test_webhook_sends_json_without_following_redirects(monkeypatch):
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", SecretStr("https://example.com/alerts"))
    monkeypatch.setattr(alert_service, "resolve_public_addresses", AsyncMock(return_value=["1.1.1.1"]))
    client_type = httpx.AsyncClient
    seen = []

    def handle(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(204)
        return httpx.Response(302, headers={"Location": "https://example.com/other"})

    monkeypatch.setattr(
        alert_service.httpx, "AsyncClient",
        lambda **options: client_type(transport=httpx.MockTransport(handle), **options),
    )
    asyncio.run(alert_service._send_webhook({"event": "endpoint.down"}, 42))
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.host == "1.1.1.1"
    assert seen[0].headers["host"] == "example.com"
    assert b'"delivery_id":42' in seen[0].content
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(alert_service._send_webhook({"event": "endpoint.down"}, 43))
    assert len(seen) == 2
    assert seen[1].url.path == "/alerts"


def test_personal_checks_do_not_send_to_shared_alert_destinations(session_factory, monkeypatch):
    from app.db.models import User

    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", SecretStr("https://example.com/alerts"))
    with session_factory() as db:
        member = User(username="member", password_hash="unused", role="member")
        db.add(member)
        db.flush()
        endpoint = Endpoint(name="Private", url="https://example.com/", owner_id=member.id)
        db.add(endpoint)
        db.flush()
        result = CheckResult(
            endpoint_id=endpoint.id,
            is_success=False,
            checked_at=datetime.now(timezone.utc),
        )
        assert alert_service.save_check_results(db, [result]) == 1
        db.commit()
        assert db.get(CheckResult, result.id) is not None
        assert db.scalars(select(AlertDelivery)).all() == []
