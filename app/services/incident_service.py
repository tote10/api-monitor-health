from datetime import datetime, timezone

from sqlalchemy import func, or_, select, type_coerce
from sqlalchemy.orm import Session

from app.api.schemas import EndpointIncidentResponse
from app.db.models import CheckResult, UTCDateTime


def list_incidents(
    db: Session, endpoint_id: int, limit: int,
) -> list[EndpointIncidentResponse]:
    checks = (
        select(
            CheckResult.id,
            CheckResult.checked_at,
            CheckResult.is_success,
            CheckResult.error,
            func.lag(CheckResult.is_success)
            .over(order_by=(CheckResult.checked_at, CheckResult.id))
            .label("previous_success"),
        )
        .where(CheckResult.endpoint_id == endpoint_id)
        .subquery()
    )
    sequence = (checks.c.checked_at, checks.c.id)
    transitions = (
        select(
            checks,
            type_coerce(
                func.lead(checks.c.checked_at).over(order_by=sequence), UTCDateTime()
            ).label("recovered_at"),
        )
        .where(
            or_(
                checks.c.previous_success.is_(None),
                checks.c.previous_success != checks.c.is_success,
            )
        )
        .subquery()
    )
    rows = db.execute(
        select(transitions)
        .where(transitions.c.is_success.is_(False))
        .order_by(transitions.c.checked_at.desc(), transitions.c.id.desc())
        .limit(limit)
    )
    now = datetime.now(timezone.utc)
    return [
        EndpointIncidentResponse(
            first_failed_at=row.checked_at,
            recovered_at=row.recovered_at,
            duration_seconds=max(0, int(((row.recovered_at or now) - row.checked_at).total_seconds())),
            started_within_history=bool(row.previous_success),
            first_error=row.error,
        )
        for row in rows
    ]
