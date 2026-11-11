from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import CheckResult


@pytest.fixture()
def endpoint(client):
    response = client.post(
        "/api/v1/endpoints",
        json={"name": "Target", "url": "https://target.example.com/ok"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _add_result(
    session_factory,
    endpoint_id,
    checked_at,
    success=True,
    response_time_ms=100,
    status_code=200,
    error=None,
):
    with session_factory() as db:
        db.add(
            CheckResult(
                endpoint_id=endpoint_id,
                checked_at=checked_at,
                is_success=success,
                status_code=status_code,
                response_time_ms=response_time_ms,
                error=error,
            )
        )
        db.commit()


def _stats(client, endpoint_id, hours=24):
    response = client.get(
        f"/api/v1/endpoints/{endpoint_id}/stats", params={"hours": hours}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_history_returns_zeroed_stats(client, endpoint):
    stats = _stats(client, endpoint["id"])

    assert stats["total_checks"] == 0
    assert stats["successful_checks"] == 0
    assert stats["uptime_percentage"] is None
    assert stats["avg_response_time_ms"] is None
    assert stats["p95_response_time_ms"] is None
    assert stats["bucket_hours"] == 1
    assert len(stats["buckets"]) == 24
    assert stats["incidents"] == []


def test_uptime_response_times_and_incidents(
    client, endpoint, session_factory
):
    now = datetime.now(timezone.utc)
    _add_result(session_factory, endpoint["id"], now - timedelta(hours=4),
                success=False, response_time_ms=100, status_code=500,
                error="Expected status 200, got 500")
    _add_result(session_factory, endpoint["id"],
                now - timedelta(hours=4) + timedelta(minutes=10),
                success=False, response_time_ms=100, status_code=500,
                error="Expected status 200, got 500")
    _add_result(session_factory, endpoint["id"], now - timedelta(hours=3),
                response_time_ms=120)
    _add_result(session_factory, endpoint["id"], now - timedelta(hours=2),
                response_time_ms=200)
    _add_result(session_factory, endpoint["id"], now - timedelta(minutes=30),
                response_time_ms=150)

    stats = _stats(client, endpoint["id"])

    assert stats["total_checks"] == 5
    assert stats["successful_checks"] == 3
    assert stats["uptime_percentage"] == 60.0
    assert stats["avg_response_time_ms"] == 134.0
    assert stats["p95_response_time_ms"] == 200.0
    assert sum(bucket["checks"] for bucket in stats["buckets"]) == 5

    assert len(stats["incidents"]) == 1
    incident = stats["incidents"][0]
    assert incident["duration_seconds"] == 600
    assert incident["ended_at"] is not None
    assert incident["status_code"] == 500
    assert "Expected status" in incident["error"]


def test_ongoing_incident_has_no_end_time(client, endpoint, session_factory):
    now = datetime.now(timezone.utc)
    _add_result(session_factory, endpoint["id"], now - timedelta(minutes=20),
                success=False, status_code=503, error="Service unavailable")
    _add_result(session_factory, endpoint["id"], now - timedelta(minutes=5),
                success=False, status_code=503, error="Service unavailable")

    incident = _stats(client, endpoint["id"])["incidents"][0]

    assert incident["ended_at"] is None
    assert incident["duration_seconds"] >= 900


def test_window_excludes_old_results(client, endpoint, session_factory):
    now = datetime.now(timezone.utc)
    _add_result(session_factory, endpoint["id"], now - timedelta(hours=30))
    _add_result(session_factory, endpoint["id"], now - timedelta(hours=2))

    assert _stats(client, endpoint["id"], hours=24)["total_checks"] == 1
    assert _stats(client, endpoint["id"], hours=48)["total_checks"] == 2


def test_longer_windows_use_larger_buckets(client, endpoint):
    stats = _stats(client, endpoint["id"], hours=168)

    assert stats["bucket_hours"] == 6
    assert len(stats["buckets"]) == 28

    monthly = _stats(client, endpoint["id"], hours=720)
    assert monthly["bucket_hours"] == 24
    assert len(monthly["buckets"]) == 30


@pytest.mark.parametrize("hours", [0, 721])
def test_window_is_validated(client, endpoint, hours):
    response = client.get(
        f"/api/v1/endpoints/{endpoint['id']}/stats", params={"hours": hours}
    )

    assert response.status_code == 422


def test_stats_for_missing_endpoint(client):
    response = client.get("/api/v1/endpoints/999/stats")

    assert response.status_code == 404
