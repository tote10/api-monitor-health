from datetime import datetime, timedelta, timezone

from app.db.models import CheckResult, Endpoint


def test_incident_timeline_groups_failures_and_reports_recovery(client, session_factory):
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    outcomes = [False, False, True, False, True, True, False, False]
    with session_factory() as db:
        endpoint = Endpoint(name="Timeline", url="https://example.com/")
        db.add(endpoint)
        db.flush()
        endpoint_id = endpoint.id
        for index, success in enumerate(outcomes):
            db.add(CheckResult(
                endpoint_id=endpoint_id,
                checked_at=start + timedelta(seconds=10 * index),
                is_success=success,
                error=None if success else "Timed out",
            ))
        db.commit()

    path = f"/api/v1/endpoints/{endpoint_id}/incidents"
    response = client.get(path)
    assert response.status_code == 200
    ongoing, recovered, oldest = response.json()
    assert ongoing["recovered_at"] is None
    assert ongoing["duration_seconds"] >= 540
    assert ongoing["first_error"] == "Timed out"
    assert recovered["duration_seconds"] == 10
    assert recovered["started_within_history"] is True
    assert oldest["duration_seconds"] == 20
    assert oldest["started_within_history"] is False
    limited = client.get(path, params={"limit": 1}).json()
    assert len(limited) == 1
    assert limited[0]["first_failed_at"] == ongoing["first_failed_at"]
