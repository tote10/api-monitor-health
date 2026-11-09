import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import CheckResult
from app.services import monitor_service


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/ok":
        return httpx.Response(200, json={"status": "ok"})
    if path == "/fail":
        return httpx.Response(500)
    if path == "/unreachable":
        raise httpx.ConnectError("connection refused", request=request)
    raise AssertionError(f"Unexpected request: {request.url}")


@pytest.fixture()
def mock_http(monkeypatch):
    def factory():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_handler),
            follow_redirects=True,
        )

    monkeypatch.setattr(monitor_service, "create_http_client", factory)


def _create_endpoint(
    client, url="https://target.example.com/ok", **overrides
):
    payload = {"name": "Target", "url": url, **overrides}
    response = client.post("/api/v1/endpoints", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _run_due_checks(session_factory):
    return asyncio.run(monitor_service.run_due_checks(session_factory))


def test_manual_check_records_success(client, mock_http):
    endpoint = _create_endpoint(client)

    response = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/check"
    )

    assert response.status_code == 201
    result = response.json()
    assert result["endpoint_id"] == endpoint["id"]
    assert result["is_success"] is True
    assert result["status_code"] == 200
    assert result["error"] is None
    assert result["response_time_ms"] >= 0
    assert result["checked_at"].endswith("Z")

    history = client.get(f"/api/v1/endpoints/{endpoint['id']}/checks")
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [result["id"]]

    status = client.get(f"/api/v1/endpoints/{endpoint['id']}/status")
    assert status.status_code == 200
    summary = status.json()
    assert summary["is_up"] is True
    assert summary["last_checked_at"].endswith("Z")
    assert summary["last_status_code"] == 200
    assert summary["total_checks"] == 1
    assert summary["successful_checks"] == 1
    assert summary["uptime_percentage"] == 100.0


def test_manual_check_records_unexpected_status(client, mock_http):
    endpoint = _create_endpoint(
        client, "https://target.example.com/fail"
    )

    result = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/check"
    ).json()

    assert result["is_success"] is False
    assert result["status_code"] == 500
    assert "Expected status 200, got 500" in result["error"]


def test_manual_check_records_network_error(client, mock_http):
    endpoint = _create_endpoint(
        client, "https://target.example.com/unreachable"
    )

    result = client.post(
        f"/api/v1/endpoints/{endpoint['id']}/check"
    ).json()

    assert result["is_success"] is False
    assert result["status_code"] is None
    assert "ConnectError" in result["error"]


def test_check_routes_return_404_for_missing_endpoint(client):
    assert client.post("/api/v1/endpoints/999/check").status_code == 404
    assert client.get("/api/v1/endpoints/999/checks").status_code == 404
    assert client.get("/api/v1/endpoints/999/status").status_code == 404


def test_status_is_empty_before_first_check(client):
    endpoint = _create_endpoint(client)

    summary = client.get(
        f"/api/v1/endpoints/{endpoint['id']}/status"
    ).json()

    assert summary["is_up"] is None
    assert summary["last_checked_at"] is None
    assert summary["total_checks"] == 0
    assert summary["successful_checks"] == 0
    assert summary["uptime_percentage"] is None


def test_check_history_is_newest_first_and_limited(client, mock_http):
    endpoint = _create_endpoint(client)
    results = [
        client.post(f"/api/v1/endpoints/{endpoint['id']}/check").json()
        for _ in range(3)
    ]

    history = client.get(
        f"/api/v1/endpoints/{endpoint['id']}/checks", params={"limit": 2}
    ).json()

    assert [item["id"] for item in history] == [
        results[2]["id"],
        results[1]["id"],
    ]

    invalid_limit = client.get(
        f"/api/v1/endpoints/{endpoint['id']}/checks", params={"limit": 0}
    )
    assert invalid_limit.status_code == 422


def test_deleting_endpoint_removes_check_history(
    client, session_factory, mock_http
):
    endpoint = _create_endpoint(client)
    client.post(f"/api/v1/endpoints/{endpoint['id']}/check")

    with session_factory() as db:
        count = db.scalar(select(func.count()).select_from(CheckResult))
    assert count == 1

    assert client.delete(
        f"/api/v1/endpoints/{endpoint['id']}"
    ).status_code == 204

    with session_factory() as db:
        count = db.scalar(select(func.count()).select_from(CheckResult))
    assert count == 0


def test_scheduler_checks_only_due_active_endpoints(
    client, session_factory, mock_http
):
    active = _create_endpoint(
        client, "https://target.example.com/ok", interval_seconds=10
    )
    inactive = _create_endpoint(
        client,
        "https://target.example.com/fail",
        interval_seconds=10,
        is_active=False,
    )

    assert _run_due_checks(session_factory) == 1

    active_status = client.get(
        f"/api/v1/endpoints/{active['id']}/status"
    ).json()
    inactive_status = client.get(
        f"/api/v1/endpoints/{inactive['id']}/status"
    ).json()
    assert active_status["total_checks"] == 1
    assert inactive_status["total_checks"] == 0

    # The interval has not elapsed yet, so nothing is checked again.
    assert _run_due_checks(session_factory) == 0

    with session_factory() as db:
        result = db.scalars(select(CheckResult)).one()
        result.checked_at = datetime.now(timezone.utc) - timedelta(
            seconds=active["interval_seconds"] + 1
        )
        db.commit()

    assert _run_due_checks(session_factory) == 1
    assert client.get(
        f"/api/v1/endpoints/{active['id']}/status"
    ).json()["total_checks"] == 2


def test_scheduler_records_batch_results(client, session_factory, mock_http):
    healthy = _create_endpoint(
        client, "https://target.example.com/ok", interval_seconds=10
    )
    broken = _create_endpoint(
        client, "https://target.example.com/unreachable", interval_seconds=10
    )

    assert _run_due_checks(session_factory) == 2

    healthy_status = client.get(
        f"/api/v1/endpoints/{healthy['id']}/status"
    ).json()
    broken_status = client.get(
        f"/api/v1/endpoints/{broken['id']}/status"
    ).json()
    assert healthy_status["is_up"] is True
    assert broken_status["is_up"] is False
    assert broken_status["last_error"].startswith("ConnectError")
