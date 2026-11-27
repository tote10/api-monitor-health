import secrets
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db.models import CheckResult, Endpoint

MAX_WORKERS_PER_POLL = 12


def claim_check(db: Session, endpoint: Endpoint, now: datetime, *, automatic: bool) -> str | None:
    token = secrets.token_hex(16)
    predicates = [
        Endpoint.id == endpoint.id,
        or_(Endpoint.check_lease_until.is_(None), Endpoint.check_lease_until <= now),
    ]
    if automatic:
        cutoff = now - timedelta(seconds=endpoint.interval_seconds + (endpoint.heartbeat_grace_seconds or 0))
        recent = select(CheckResult.id).where(
            CheckResult.endpoint_id == Endpoint.id, CheckResult.checked_at > cutoff,
        ).correlate(Endpoint).exists()
        predicates.extend((Endpoint.is_active.is_(True),
                           Endpoint.interval_seconds == endpoint.interval_seconds,
                           Endpoint.check_epoch == endpoint.check_epoch, ~recent))
    claimed = db.execute(
        update(Endpoint).where(*predicates)
        .values(check_token=token, check_lease_until=now + timedelta(seconds=90))
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return token if claimed else None


def release_claim(db: Session, endpoint_id: int, token: str) -> bool:
    return bool(db.execute(
        update(Endpoint).where(Endpoint.id == endpoint_id, Endpoint.check_token == token)
        .values(check_token=None, check_lease_until=None, check_epoch=Endpoint.check_epoch + 1)
        .execution_options(synchronize_session=False)
    ).rowcount)



def latest_check_at():
    return select(CheckResult.checked_at).where(CheckResult.endpoint_id == Endpoint.id).order_by(
        CheckResult.checked_at.desc(), CheckResult.id.desc(),
    ).limit(1).correlate(Endpoint).scalar_subquery()
