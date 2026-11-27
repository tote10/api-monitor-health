from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import AlertDelivery, Endpoint, SchedulerWorker
from app.services.check_claims import latest_check_at


def record_poll(worker_id: str, started_at: datetime, state: str, saved: int) -> None:
    with SessionLocal() as db:
        db.merge(SchedulerWorker(
            id=worker_id, started_at=started_at, last_poll_at=datetime.now(timezone.utc),
            state=state, last_saved=saved,
        ))
        db.commit()


def scheduler_diagnostics(db: Session) -> dict:
    now = datetime.now(timezone.utc)
    recent = now - timedelta(days=1)
    late_worker = now - timedelta(seconds=max(90, settings.SCHEDULER_POLL_SECONDS * 3))
    endpoints = db.execute(select(
        Endpoint.interval_seconds, Endpoint.heartbeat_grace_seconds, Endpoint.created_at,
        Endpoint.check_lease_until, latest_check_at().label("latest"),
    ).where(Endpoint.is_active.is_(True), Endpoint.kind != "agent"))
    overdue = []
    unclaimed = 0
    missed = 0
    for interval, grace, created, lease, latest in endpoints:
        cadence = interval + (grace or 0)
        due = (latest or created) + timedelta(seconds=cadence if latest or grace is not None else 0)
        lag = (now - due).total_seconds()
        if lag >= 0:
            overdue.append(lag)
            unclaimed += int(not lease or lease <= now)
            missed += int(lag // cadence)
    statuses = dict(db.execute(select(
        AlertDelivery.state, func.count(),
    ).group_by(AlertDelivery.state)).all())
    workers = db.scalars(select(SchedulerWorker).where(SchedulerWorker.last_poll_at >= recent)
                         .order_by(SchedulerWorker.last_poll_at.desc()).limit(100))
    return {
        "scheduler_enabled": settings.MONITORING_ENABLED,
        "queued_checks": unclaimed,
        "overdue_monitors": len(overdue),
        "max_lag_seconds": round(max(overdue, default=0), 1),
        "estimated_missed_checks": missed,
        "pending_alerts": statuses.get("pending", 0),
        "failed_alerts": statuses.get("failed", 0),
        "workers": [
            {"id": worker.id, "state": worker.state if worker.last_poll_at >= late_worker else "offline",
             "last_poll_at": worker.last_poll_at, "last_saved": worker.last_saved}
            for worker in workers
        ],
        "timing_breakdown": {"dns": None, "connect": None, "tls": None, "ttfb": None},
    }
