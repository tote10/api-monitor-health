import json

import httpx
import pytest

from app.core.config import settings
from app.services import monitor_service, notification_service


@pytest.fixture()
def target_http(monkeypatch):
    def handler(request):
        if request.url.path == "/fail":
            return httpx.Response(500)
        return httpx.Response(200)

    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(monitor_service, "create_http_client", factory)


@pytest.fixture()
def webhooks(monkeypatch):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200)

    def factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(notification_service, "create_webhook_client", factory)
    return sent


def _create_endpoint(client, path="/ok"):
    response = client.post(
        "/api/v1/endpoints",
        json={"name": f"Target {path}", "url": f"https://target.example.com{path}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_webhook_channel(client):
    response = client.post(
        "/api/v1/channels",
        json={
            "name": "Team chat",
            "kind": "webhook",
            "target": "https://hooks.example.com/alerts",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _check(client, endpoint_id):
    response = client.post(f"/api/v1/endpoints/{endpoint_id}/check")
    assert response.status_code == 201, response.text
    return response.json()


def test_channel_crud_and_validation(client):
    created = _create_webhook_channel(client)
    assert created["is_enabled"] is True
    assert created["last_sent_at"] is None

    listed = client.get("/api/v1/channels")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]

    updated = client.patch(
        f"/api/v1/channels/{created['id']}", json={"is_enabled": False}
    )
    assert updated.status_code == 200
    assert updated.json()["is_enabled"] is False

    assert client.delete(
        f"/api/v1/channels/{created['id']}"
    ).status_code == 204
    assert client.get("/api/v1/channels").json() == []
    assert client.delete(
        f"/api/v1/channels/{created['id']}"
    ).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Bad email", "kind": "email", "target": "not-an-email"},
        {"name": "Bad hook", "kind": "webhook", "target": "ftp://example.com"},
        {"name": "No scheme", "kind": "webhook", "target": "hooks.example.com"},
    ],
)
def test_channel_targets_are_validated(client, payload):
    response = client.post("/api/v1/channels", json=payload)

    assert response.status_code == 400


def test_channel_update_validates_new_target(client):
    channel = client.post(
        "/api/v1/channels",
        json={
            "name": "Email",
            "kind": "email",
            "target": "ops@example.com",
        },
    ).json()

    response = client.patch(
        f"/api/v1/channels/{channel['id']}", json={"target": "broken"}
    )
    assert response.status_code == 400


def test_test_alert_reports_delivery(client, webhooks):
    channel = _create_webhook_channel(client)

    response = client.post(f"/api/v1/channels/{channel['id']}/test")

    assert response.status_code == 200
    assert response.json() == {"sent": True, "error": None}
    assert webhooks[0]["event"] == "test"
    assert webhooks[0]["text"]

    refreshed = client.get("/api/v1/channels").json()[0]
    assert refreshed["last_sent_at"] is not None
    assert refreshed["last_error"] is None


def test_failure_and_recovery_alerts_follow_state_changes(
    client, webhooks, target_http
):
    endpoint = _create_endpoint(client, "/fail")
    _create_webhook_channel(client)

    _check(client, endpoint["id"])
    assert len(webhooks) == 1
    assert webhooks[0]["event"] == "endpoint_down"
    assert webhooks[0]["endpoint"]["status_code"] == 500
    assert "DOWN" in webhooks[0]["text"]

    # Still failing: no new alert.
    _check(client, endpoint["id"])
    assert len(webhooks) == 1

    healthy = _create_endpoint(client, "/ok")
    _check(client, healthy["id"])
    assert len(webhooks) == 1  # first check of a healthy target is silent


def test_first_healthy_check_is_silent(client, webhooks, target_http):
    endpoint = _create_endpoint(client, "/ok")
    _create_webhook_channel(client)

    _check(client, endpoint["id"])
    assert webhooks == []


def test_cooldown_suppresses_flapping_alerts(client, webhooks, target_http):
    endpoint = _create_endpoint(client, "/fail")
    _create_webhook_channel(client)

    _check(client, endpoint["id"])  # down
    assert len(webhooks) == 1

    client.patch(
        f"/api/v1/endpoints/{endpoint['id']}",
        json={"url": "https://target.example.com/ok"},
    )
    _check(client, endpoint["id"])  # recovery
    assert len(webhooks) == 2
    assert webhooks[1]["event"] == "endpoint_recovered"

    client.patch(
        f"/api/v1/endpoints/{endpoint['id']}",
        json={"url": "https://target.example.com/fail"},
    )
    _check(client, endpoint["id"])  # down again, inside the cooldown
    assert len(webhooks) == 2


def test_cooldown_can_be_disabled(client, webhooks, target_http, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_COOLDOWN_MINUTES", 0)
    endpoint = _create_endpoint(client, "/fail")
    _create_webhook_channel(client)

    _check(client, endpoint["id"])
    client.patch(
        f"/api/v1/endpoints/{endpoint['id']}",
        json={"url": "https://target.example.com/ok"},
    )
    _check(client, endpoint["id"])
    client.patch(
        f"/api/v1/endpoints/{endpoint['id']}",
        json={"url": "https://target.example.com/fail"},
    )
    _check(client, endpoint["id"])

    assert [item["event"] for item in webhooks] == [
        "endpoint_down",
        "endpoint_recovered",
        "endpoint_down",
    ]


def test_alerts_can_be_disabled_per_endpoint(client, webhooks, target_http):
    endpoint = _create_endpoint(client, "/fail")
    client.patch(
        f"/api/v1/endpoints/{endpoint['id']}",
        json={"alerts_enabled": False},
    )
    _create_webhook_channel(client)

    _check(client, endpoint["id"])

    assert webhooks == []


def test_disabled_channels_are_skipped(client, webhooks, target_http):
    endpoint = _create_endpoint(client, "/fail")
    channel = _create_webhook_channel(client)
    client.patch(
        f"/api/v1/channels/{channel['id']}", json={"is_enabled": False}
    )

    _check(client, endpoint["id"])

    assert webhooks == []


def test_scheduled_checks_alert_on_transition(
    client, webhooks, target_http, session_factory
):
    import asyncio

    endpoint = _create_endpoint(client, "/fail")
    _create_webhook_channel(client)

    asyncio.run(monitor_service.run_due_checks(session_factory))

    assert len(webhooks) == 1
    assert webhooks[0]["endpoint"]["id"] == endpoint["id"]
    assert webhooks[0]["event"] == "endpoint_down"


def test_email_channel_reports_missing_smtp_configuration(client):
    channel = client.post(
        "/api/v1/channels",
        json={
            "name": "On-call",
            "kind": "email",
            "target": "oncall@example.com",
        },
    ).json()

    response = client.post(f"/api/v1/channels/{channel['id']}/test")

    assert response.status_code == 200
    body = response.json()
    assert body["sent"] is False
    assert "SMTP" in body["error"]


def test_email_alerts_use_configured_smtp(
    client, webhooks, target_http, monkeypatch
):
    sent_mail = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def starttls(self):
            pass

        def login(self, username, password):
            pass

        def send_message(self, message):
            sent_mail.append(message)

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notification_service.smtplib, "SMTP", FakeSMTP)

    endpoint = _create_endpoint(client, "/fail")
    client.post(
        "/api/v1/channels",
        json={
            "name": "On-call",
            "kind": "email",
            "target": "oncall@example.com",
        },
    )

    _check(client, endpoint["id"])

    assert len(sent_mail) == 1
    assert sent_mail[0]["To"] == "oncall@example.com"
    assert sent_mail[0]["Subject"].startswith("DOWN:")
    channels = client.get("/api/v1/channels").json()
    assert channels[0]["last_sent_at"] is not None
