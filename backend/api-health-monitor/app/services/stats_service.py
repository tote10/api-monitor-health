"""Uptime, response-time and incident aggregations for the dashboard."""

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import EndpointStatsResponse, IncidentSummary, StatsBucket
from app.db.models import CheckResult

MAX_INCIDENTS = 10


def bucket_hours_for(window_hours: int) -> int:
    """Pick a bucket size that keeps charts readable for each time range."""
    if window_hours <= 24:
        return 1
    if window_hours <= 168:
        return 6
    return 24


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentage(part: int, total: int) -> float | None:
    return round(part / total * 100, 2) if total else None


def _p95(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return float(ordered[index])


def build_endpoint_stats(
    db: Session, endpoint_id: int, window_hours: int
) -> EndpointStatsResponse:
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(hours=window_hours)).replace(
        minute=0, second=0, microsecond=0
    )
    bucket_hours = bucket_hours_for(window_hours)
    bucket_seconds = bucket_hours * 3600
    bucket_count = max(1, math.ceil(window_hours / bucket_hours))

    rows = db.execute(
        select(
            CheckResult.checked_at,
            CheckResult.is_success,
            CheckResult.response_time_ms,
            CheckResult.status_code,
            CheckResult.error,
        )
        .where(
            CheckResult.endpoint_id == endpoint_id,
            CheckResult.checked_at >= window_start,
        )
        .order_by(CheckResult.checked_at, CheckResult.id)
    ).all()

    counters = [
        {"checks": 0, "failures": 0, "response_ms": []} for _ in range(bucket_count)
    ]
    response_times: list[int] = []
    failures = 0
    incidents: list[IncidentSummary] = []
    open_incident: dict | None = None

    for row in rows:
        checked_at = _as_utc(row.checked_at)
        index = int((checked_at - window_start).total_seconds() // bucket_seconds)
        index = min(max(index, 0), bucket_count - 1)

        bucket = counters[index]
        bucket["checks"] += 1
        if not row.is_success:
            bucket["failures"] += 1
            failures += 1
        if row.response_time_ms is not None:
            bucket["response_ms"].append(row.response_time_ms)
            response_times.append(row.response_time_ms)

        if row.is_success:
            if open_incident is not None:
                incidents.append(_close_incident(open_incident))
                open_incident = None
        else:
            if open_incident is None:
                open_incident = {
                    "started_at": checked_at,
                    "last_failure_at": checked_at,
                    "status_code": row.status_code,
                    "error": row.error,
                }
            else:
                open_incident["last_failure_at"] = checked_at
                open_incident["status_code"] = row.status_code
                open_incident["error"] = row.error

    if open_incident is not None:
        incidents.append(_close_incident(open_incident, ongoing_at=now))

    buckets = [
        StatsBucket(
            start=window_start + timedelta(seconds=index * bucket_seconds),
            checks=bucket["checks"],
            failures=bucket["failures"],
            uptime_percentage=_percentage(
                bucket["checks"] - bucket["failures"], bucket["checks"]
            ),
            avg_response_time_ms=(
                round(sum(bucket["response_ms"]) / len(bucket["response_ms"]), 1)
                if bucket["response_ms"]
                else None
            ),
        )
        for index, bucket in enumerate(counters)
    ]

    total_checks = len(rows)
    return EndpointStatsResponse(
        endpoint_id=endpoint_id,
        window_hours=window_hours,
        bucket_hours=bucket_hours,
        total_checks=total_checks,
        successful_checks=total_checks - failures,
        uptime_percentage=_percentage(total_checks - failures, total_checks),
        avg_response_time_ms=(
            round(sum(response_times) / len(response_times), 1)
            if response_times
            else None
        ),
        p95_response_time_ms=_p95(response_times),
        buckets=buckets,
        incidents=incidents[-MAX_INCIDENTS:][::-1],
    )


def _close_incident(
    incident: dict, ongoing_at: datetime | None = None
) -> IncidentSummary:
    """Turn a run of consecutive failures into an incident summary."""
    last_failure_at = incident["last_failure_at"]
    end = ongoing_at or last_failure_at

    return IncidentSummary(
        started_at=incident["started_at"],
        ended_at=None if ongoing_at else last_failure_at,
        duration_seconds=max(int((end - incident["started_at"]).total_seconds()), 0),
        status_code=incident["status_code"],
        error=incident["error"],
    )
