import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
import re
from socketserver import StreamRequestHandler, ThreadingTCPServer
from threading import Thread
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import AlertAttempt, AlertDelivery, CheckResult, EmailMonitor, EmailRecipient, Endpoint, User
from app.services import alert_service, monitor_service
from app.services.email_notifications import queue_weekly_digests
from main import app


class CaptureSMTP(StreamRequestHandler):
    def respond(self, status: bytes):
        self.wfile.write(status + b"\r\n")

    def handle(self):
        self.respond(b"220 localhost test capture")
        recipient = ""
        while line := self.rfile.readline():
            command = line.partition(b" ")[0].strip().upper()
            if command in {b"EHLO", b"HELO", b"MAIL", b"RSET"}:
                self.respond(b"250 OK")
            elif command == b"RCPT":
                recipient = line.split(b"<", 1)[1].split(b">", 1)[0].decode()
                self.respond(self.server.refusals.get(recipient, b"250 OK"))
            elif command == b"DATA":
                self.respond(b"354 Send message")
                lines = []
                while (data := self.rfile.readline()) != b".\r\n":
                    if not data:
                        return
                    lines.append(data[1:] if data.startswith(b"..") else data)
                message = BytesParser(policy=policy.default).parsebytes(b"".join(lines))
                self.server.messages.append((recipient, message))
                self.respond(b"250 Accepted")
            elif command == b"QUIT":
                self.respond(self.server.quit_response)
                return
            else:
                self.respond(b"502 Unsupported command")


@pytest.fixture
def mailbox(monkeypatch):
    with ThreadingTCPServer(("127.0.0.1", 0), CaptureSMTP) as server:
        server.messages = []
        server.refusals = {}
        server.quit_response = b"221 Goodbye"
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(settings, "SMTP_HOST", "127.0.0.1")
        monkeypatch.setattr(settings, "SMTP_PORT", server.server_address[1])
        monkeypatch.setattr(settings, "SMTP_SECURITY", "plain")
        monkeypatch.setattr(settings, "SMTP_FROM", "monitor@example.test")
        monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://testserver")
        monkeypatch.setattr(settings, "ALERT_EMAIL_TO", "")
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def plain(message):
    return message.get_body(preferencelist=("plain",)).get_content()


def mail_token(message, action):
    return re.search(rf"/email/{action}#([A-Za-z0-9_.-]+)", plain(message))[1]


def send_pending(session_factory):
    asyncio.run(alert_service.deliver_pending(session_factory))


def fake_checks(monkeypatch, *states):
    results = iter(states)

    async def check(endpoint, client):
        state = next(results)
        checked_at = datetime.now(timezone.utc)
        if state:
            return CheckResult(
                endpoint_id=endpoint.id, checked_at=checked_at, is_success=True, status_code=200, response_time_ms=51,
            )
        return CheckResult(
            endpoint_id=endpoint.id, checked_at=checked_at, is_success=False, status_code=503, response_time_ms=170,
            failure_kind="http_status", error=f"PRIVATE_RAW_BODY password=secret123 url={endpoint.url}",
        )

    monkeypatch.setattr(monitor_service, "check_endpoint", check)


def add_monitor(client, name, url, **extra):
    response = client.post("/api/v1/endpoints", json={"name": name, "url": url, **extra})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_verified_monitor_outage_to_recovery(client, mailbox, session_factory, monkeypatch):
    endpoint_id = add_monitor(client, "Checkout API", "https://internal.example.test/check?token=secret123")
    other_id = add_monitor(client, "Other API", "https://other.example.test/health")
    address = "ops@example.test"
    assert client.post("/operations/email/recipients", json={"address": address.upper()}).status_code == 202
    assert client.post("/operations/email/recipients", json={"address": address}).json() == {"queued": False}
    recipient = client.get("/operations/email").json()["recipients"][0]
    recipient_path = f"/operations/email/recipients/{recipient['id']}"
    assert client.post(recipient_path + "/test").status_code == 409
    send_pending(session_factory)
    assert len(mailbox.messages) == 1
    confirmation = mail_token(mailbox.messages[0][1], "confirm")
    assert client.get("/email/confirm", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200
    assert client.post("/email/confirm", json={"token": confirmation}).status_code == 200
    assert client.post("/email/confirm", json={"token": confirmation}).status_code == 404
    assert client.put(recipient_path, json={"monitor_ids": [endpoint_id + other_id]}).status_code == 404
    assert client.put(recipient_path, json={"monitor_ids": [endpoint_id], "weekly_digest": True}).json()["verified"]
    assert client.post(recipient_path + "/test").status_code == 202
    mailbox.quit_response = b"451 QUIT unavailable"
    send_pending(session_factory)
    test_result = client.get("/operations/email").json()["recipients"][0]["last_test"]
    assert test_result["state"] == "sent" and test_result["accepted_at"]
    assert [msg["Subject"] for _, msg in mailbox.messages] == ["Confirm your email address", "Test monitoring email"]
    mailbox.messages.clear()

    # Legacy shared email must not duplicate a verified monitor subscription.
    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", address)
    fake_checks(monkeypatch, True, False, False, True)
    path = f"/api/v1/endpoints/{endpoint_id}"
    overview_path = "/api/v1/endpoints/operations/overview"
    assert client.post(path + "/check").status_code == 201
    overview = client.get(overview_path).json()
    assert overview["active_incidents"] == []
    assert overview["monitors"][0]["status"]["is_up"] is True
    assert overview["monitors"][1]["status"]["is_up"] is None
    assert overview["monitors"][1]["recent_checks"] == []
    assert client.post(path + "/check").status_code == 201
    outage = client.get(overview_path).json()
    assert outage["monitors"][0]["status"]["is_up"] is False
    incident_id = outage["active_incidents"][0]["id"]
    incident_path = f"{path}/incident-records/{incident_id}"
    assert client.post(incident_path + "/acknowledge").status_code == 200
    send_pending(session_factory)
    assert client.post(path + "/check").status_code == 201
    send_pending(session_factory)
    assert len(mailbox.messages) == 1

    assert client.post(path + "/check").status_code == 201
    send_pending(session_factory)
    overview = client.get(overview_path).json()
    assert not overview["active_incidents"]
    assert overview["monitors"][0]["status"]["is_up"] is True
    assert [check["ok"] for check in overview["monitors"][0]["recent_checks"]] == [True, False, False, True]
    assert [recipient for recipient, _ in mailbox.messages] == [address, address]
    down, up = [message for _, message in mailbox.messages]
    assert [down["Subject"], up["Subject"]] == ["Checkout API is down", "Checkout API is recovered"]
    assert "Unexpected HTTP status 503" in plain(down)
    assert f"http://testserver/?endpoint={endpoint_id}&incident={incident_id}" in plain(down)
    assert "Detected at" in plain(down)
    assert "Resolved at" in plain(up) and "Duration:" in plain(up)
    for message in (down, up):
        assert message.get_body(preferencelist=("html",)).get_content()
        for value in ("PRIVATE_RAW_BODY", "secret123", "internal.example.test"):
            assert value not in message.as_string()

    timeline = client.get(incident_path + "/timeline").json()["events"]
    assert [event["kind"] for event in timeline] == [
        "failed_check", "opened", "acknowledged", "notification", "failed_check", "recovered", "notification",
    ]
    assert [event["outcome"] for event in timeline if event["kind"] == "notification"] == ["accepted", "accepted"]
    client.post("/auth/logout")
    url = f"/?endpoint={endpoint_id}&incident={incident_id}"
    redirect = client.get(url, headers={"Accept": "text/html"}, follow_redirects=False)
    assert redirect.headers["location"] == "/login" + url[1:]


def test_public_subscriptions_only_send_public_details(client, mailbox, session_factory, monkeypatch):
    endpoint_id = add_monitor(client, "PRIVATE_SERVICE_NAME", "https://internal.example.test/health", public_label="API")
    other_id = add_monitor(client, "Hidden endpoint", "https://other.example.test/health")
    page = client.put("/operations/status-settings", json={"title": "Status", "enabled": True}).json()["path"]
    with closing(TestClient(app)) as visitor:
        target = f"{page}/services/{endpoint_id}/subscribe"
        hidden = f"{page}/services/{other_id}/subscribe"
        signup = {"address": "visitor@example.test"}
        assert visitor.post(hidden, json=signup).status_code == 404
        assert visitor.post(target, json=signup, headers={"Origin": "https://other.test"}).status_code == 403
        assert visitor.post(target, json=signup).status_code == 202
        assert visitor.post(target, json=signup).status_code == 202
        send_pending(session_factory)
        assert len(mailbox.messages) == 1
        fake_checks(monkeypatch, False, True, False, True, False)
        path = f"/api/v1/endpoints/{endpoint_id}/check"
        assert client.post(path).status_code == 201
        send_pending(session_factory)
        assert len(mailbox.messages) == 1
        token = mail_token(mailbox.messages[0][1], "confirm")
        assert visitor.post("/email/confirm", json={"token": token}).status_code == 200
        for _ in range(3):
            assert client.post(path).status_code == 201
            send_pending(session_factory)
        assert [msg["Subject"] for _, msg in mailbox.messages[1:]] == [
            "API is recovered", "API is down", "API is recovered",
        ]
        for _, message in mailbox.messages[1:]:
            body = plain(message)
            for private in ("PRIVATE_SERVICE_NAME", "PRIVATE_RAW_BODY", "HTTP status", "internal.example.test", "?endpoint="):
                assert private not in body
            assert f"{page}#service-{endpoint_id}" in body
        token = mail_token(mailbox.messages[-1][1], "unsubscribe")
        assert visitor.post("/email/unsubscribe", json={"token": token}).status_code == 200
        assert client.post(path).status_code == 201
        send_pending(session_factory)
        assert len(mailbox.messages) == 4


def test_weekly_digest_and_smtp_rejection_history(mailbox, session_factory):
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start = monday - timedelta(days=7)
    with session_factory() as db:
        user = User(username="operator", password_hash="unused", role="member")
        db.add(user)
        db.flush()
        endpoint = Endpoint(owner_id=user.id, name="Checkout", url="https://example.test/checkout")
        unknown = Endpoint(owner_id=user.id, name="Unknown service", url="https://example.test/unknown")
        recipient = EmailRecipient(
            scope=f"user:{user.id}", user_id=user.id, address="digest@example.test",
            verified_at=start, weekly_digest=True,
        )
        db.add_all([endpoint, unknown, recipient])
        db.flush()
        db.add_all([EmailMonitor(recipient_id=recipient.id, endpoint_id=item.id) for item in (endpoint, unknown)])
        for state in (True, False):
            db.add(CheckResult(endpoint_id=endpoint.id, checked_at=start + timedelta(days=1), is_success=state))
        db.commit()
        queue_weekly_digests(db, now)
        db.commit()
        queue_weekly_digests(db, now)
        db.commit()
        delivery = db.scalars(select(AlertDelivery)).one()
        delivery.next_attempt_at = start
        delivery_id = delivery.id
        db.commit()

    mailbox.refusals[recipient.address] = b"451 Private provider detail"
    send_pending(session_factory)
    with session_factory() as db:
        delivery = db.get(AlertDelivery, delivery_id)
        assert delivery.state == "pending" and delivery.accepted_at is None
        assert delivery.last_error == "SMTP 451"
        delivery.next_attempt_at = start
        db.commit()
    mailbox.refusals.clear()
    send_pending(session_factory)
    assert len(mailbox.messages) == 1
    message = mailbox.messages[0][1]
    assert message["Subject"] == "Weekly service digest"
    assert "Checkout: 50.0%" in plain(message) and "Unknown service: Unknown" in plain(message)
    with session_factory() as db:
        delivery = db.get(AlertDelivery, delivery_id)
        assert delivery.state == "sent" and delivery.accepted_at
        attempts = db.scalars(select(AlertAttempt).order_by(AlertAttempt.number)).all()
        assert [(attempt.outcome, attempt.error) for attempt in attempts] == [("failed", "SMTP 451"), ("accepted", None)]



def test_smtp_rejection_and_expired_final_lease_do_not_add_attempts(mailbox, session_factory):
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        user = User(username="operator", password_hash="unused", role="member")
        db.add(user)
        db.flush()
        recipient = EmailRecipient(scope=f"user:{user.id}", user_id=user.id, address="reject@example.test", verified_at=now)
        db.add(recipient)
        db.flush()
        first = AlertDelivery(
            recipient_id=recipient.id, channel="email", payload={"event": "email.test"}, next_attempt_at=now,
        )
        expired = AlertDelivery(
            recipient_id=recipient.id, channel="email", payload={"event": "email.test"}, next_attempt_at=now,
            attempts=alert_service.MAX_ATTEMPTS, delivery_lease_until=now - timedelta(seconds=1), last_attempt_at=now,
        )
        db.add_all([first, expired])
        db.flush()
        db.add(AlertAttempt(delivery_id=expired.id, number=alert_service.MAX_ATTEMPTS, started_at=now))
        ids = (first.id, expired.id)
        db.commit()
    mailbox.refusals[recipient.address] = b"550 Provider details are private"
    send_pending(session_factory)
    send_pending(session_factory)
    assert mailbox.messages == []
    with session_factory() as db:
        first, expired = [db.get(AlertDelivery, item) for item in ids]
        assert (first.state, first.attempts, first.last_error) == ("failed", 1, "SMTP 550")
        assert (expired.state, expired.attempts) == ("failed", alert_service.MAX_ATTEMPTS)
        assert first.accepted_at is None and expired.accepted_at is None
        assert db.get(AlertAttempt, (expired.id, alert_service.MAX_ATTEMPTS)).outcome == "unknown"

def test_demo_isolates_sessions_and_never_calls_external_transports(anonymous_client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "DEMO_ENABLED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "production.example")
    monkeypatch.setattr(settings, "SMTP_FROM", "production@example.test")
    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", "real@example.test")
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URL", SecretStr("https://webhook.example.test"))
    smtp = Mock(side_effect=AssertionError("Demo called SMTP"))
    webhook = AsyncMock(side_effect=AssertionError("Demo called webhook"))
    monkeypatch.setattr(alert_service, "_send_email", smtp)
    monkeypatch.setattr(alert_service, "_send_webhook", webhook)
    with session_factory() as db:
        db.add(Endpoint(name="Real monitor", url="https://example.test/secret"))
        db.commit()
    assert anonymous_client.get("/demo").status_code == 200
    assert anonymous_client.post("/demo/outage").status_code == 410
    assert anonymous_client.get("/demo/state", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    initial = anonymous_client.get("/demo/state").json()
    assert initial["status"]["is_up"] is True and initial["messages"] == []
    outage = anonymous_client.post("/demo/outage").json()
    assert not outage["status"]["is_up"] and outage["active_incident"]
    repeated = anonymous_client.post("/demo/outage").json()
    assert len(repeated["messages"]) == 1
    with closing(TestClient(app)) as other:
        separate = other.get("/demo/state").json()
        assert separate["status"]["is_up"] and separate["messages"] == []
    recovery = anonymous_client.post("/demo/restore").json()
    assert recovery["status"]["is_up"] and not recovery["active_incident"]
    assert [msg["subject"] for msg in recovery["messages"]] == ["Demo API is recovered", "Demo API is down"]
    assert {msg["to"] for msg in recovery["messages"]} == {"visitor@example.test"}
    assert "Unexpected HTTP status 503" in recovery["messages"][1]["html"]
    assert len([evt for evt in recovery["timeline"]["events"] if evt["kind"] == "failed_check"]) == 2
    smtp.assert_not_called()
    webhook.assert_not_called()
    with session_factory() as db:
        assert db.scalars(select(CheckResult)).all() == []
        assert db.scalars(select(AlertDelivery)).all() == []
