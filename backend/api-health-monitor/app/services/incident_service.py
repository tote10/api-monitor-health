from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, type_coerce
from sqlalchemy.orm import Session

from app.api.schemas import EndpointIncidentResponse
from app.db.models import AlertAttempt, AlertDelivery, CheckResult, Endpoint, Incident, UTCDateTime


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


def transition_incident(db: Session, endpoint: Endpoint, result: CheckResult) -> Incident | None:
    """Evaluate a persisted check while the endpoint write claim is held."""
    active = db.scalar(select(Incident).where(
        Incident.endpoint_id == endpoint.id, Incident.recovered_at.is_(None),
    ))
    if result.is_success != (active is not None):
        return None
    threshold = endpoint.recovery_threshold if active else endpoint.failure_threshold
    recent = list(db.scalars(select(CheckResult).where(
        CheckResult.endpoint_id == endpoint.id,
        or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured"),
    ).order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(threshold)))
    if recent[0].id != result.id or len(recent) < threshold:
        return None
    if any(check.is_success != result.is_success for check in recent):
        return None
    if active:
        if recent[-1].checked_at < active.opened_at:
            return None
        active.recovered_at = result.checked_at
        return active
    previous_recovery = db.scalar(select(func.max(Incident.recovered_at)).where(
        Incident.endpoint_id == endpoint.id,
    ))
    if previous_recovery is not None and recent[-1].checked_at <= previous_recovery:
        return None
    incident = Incident(
        endpoint_id=endpoint.id, first_failed_at=recent[-1].checked_at,
        opened_at=result.checked_at, first_error=recent[-1].error,
    )
    db.add(incident)
    db.flush()
    return incident


def incident_timeline(db: Session, incident: Incident) -> dict:
    limit = 200
    failures = select(CheckResult).where(
        CheckResult.endpoint_id == incident.endpoint_id, CheckResult.is_success.is_(False),
        or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured"),
        # SQLite can store an older server-default timestamp without fractional seconds.
        CheckResult.checked_at > incident.first_failed_at - timedelta(microseconds=1),
    )
    if incident.recovered_at:
        failures = failures.where(CheckResult.checked_at <= incident.recovered_at)
    checks = list(db.scalars(failures.order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(limit + 1)))

    deliveries = list(db.scalars(select(AlertDelivery).where(
        AlertDelivery.endpoint_id == incident.endpoint_id, AlertDelivery.incident_id == incident.id,
    ).order_by(AlertDelivery.id.desc()).limit(limit + 1)))
    attempts = db.scalars(select(AlertAttempt).where(
        AlertAttempt.delivery_id.in_([item.id for item in deliveries[:limit]]),
    ).order_by(AlertAttempt.started_at.desc()).limit(limit + 1)).all()

    events = [{"kind": "failed_check", "at": check.checked_at, "detail": check.error or "Check failed"}
              for check in checks[:limit]]
    events.append({"kind": "opened", "at": incident.opened_at, "detail": "Incident opened"})
    if incident.acknowledged_at:
        events.append({"kind": "acknowledged", "at": incident.acknowledged_at, "detail": "Incident acknowledged"})
    if incident.recovered_at:
        events.append({"kind": "recovered", "at": incident.recovered_at, "detail": "Service recovered"})

    by_id = {delivery.id: delivery for delivery in deliveries}
    for attempt in attempts[:limit]:
        delivery = by_id[attempt.delivery_id]
        events.append({
            "kind": "notification", "at": attempt.started_at, "channel": delivery.channel,
            "event": delivery.payload["event"], "outcome": attempt.outcome, "number": attempt.number,
            "detail": attempt.error, "finished_at": attempt.finished_at,
        })
    seen = {attempt.delivery_id for attempt in attempts}
    for delivery in deliveries[:limit]:
        if delivery.id in seen or (len(attempts) > limit and delivery.attempts):
            continue
        events.append({
            "kind": "notification", "at": delivery.last_attempt_at or delivery.created_at,
            "channel": delivery.channel, "event": delivery.payload["event"],
            "outcome": delivery.state, "number": delivery.attempts, "detail": delivery.last_error,
        })
    return {
        "events": sorted(events, key=lambda event: event["at"]),
        "truncated": max(len(checks), len(deliveries), len(attempts)) > limit,
    }
