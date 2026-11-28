import asyncio
import logging
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from cryptography.fernet import InvalidToken
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.schemas import EndpointStatusResponse, RequestOptions
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import CheckResult, Endpoint, PrivateAgent
from app.services.agent_health import agent_is_online
from app.services.alert_service import save_check_results
from app.services.check_claims import MAX_WORKERS_PER_POLL, claim_check, latest_check_at, release_claim
from app.services.diagnostics import record_poll
from app.services.http_checks import Observation, execute_http_check
from app.services.request_config import monitor_cipher

logger = logging.getLogger(__name__)
# HTTPX request logs include the full request URL.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class EndpointRemovedDuringCheck(Exception):
    """The endpoint was removed before its check could be stored."""


class CheckInProgress(Exception):
    """An active worker already owns this check."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """Treat timestamps read back from the database as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def create_http_client() -> httpx.AsyncClient:
    """Create the HTTP client used for checks (overridable in tests)."""
    return httpx.AsyncClient(
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
    )


async def check_endpoint(
    endpoint: Endpoint, client: httpx.AsyncClient, *, fetch=None,
) -> CheckResult:
    """Call an endpoint once and build an unsaved check result."""
    if endpoint.kind == "heartbeat":
        return CheckResult(
            endpoint_id=endpoint.id, checked_at=_utcnow(), is_success=False,
            failure_kind="missed_heartbeat", error="Expected heartbeat did not arrive before its deadline",
        )
    started = time.perf_counter()
    try:
        options = RequestOptions()
        if endpoint.request_config is not None:
            options = RequestOptions.model_validate_json(
                monitor_cipher().decrypt(endpoint.request_config.encode())
            )
        result = await execute_http_check(client, endpoint, options, fetch=fetch)
    except (InvalidToken, ValueError):
        result = Observation(None, "configuration", "Request settings could not be loaded")

    response_time_ms = max(0, round((time.perf_counter() - started) * 1000))

    return CheckResult(
        endpoint_id=endpoint.id,
        checked_at=_utcnow(),
        is_success=result.error is None,
        status_code=result.status_code,
        response_time_ms=response_time_ms,
        error=result.error,
        failure_kind=result.failure_kind,
        failed_step=result.failed_step,
        certificate_expires_at=result.certificate_expires_at,
        is_slow=result.error is None and endpoint.latency_budget_ms is not None
        and response_time_ms > endpoint.latency_budget_ms,
    )


async def perform_check(
    db: Session, endpoint: Endpoint, *, probe=None, demo: bool = False,
) -> CheckResult:
    """Run a check immediately and persist the result."""
    token = claim_check(db, endpoint, _utcnow(), automatic=False)
    if token is None:
        raise CheckInProgress
    try:
        async with create_http_client() as client:
            result = await (probe or check_endpoint)(endpoint, client)
        if not release_claim(db, endpoint.id, token) or not save_check_results(db, [result], demo=demo):
            raise EndpointRemovedDuringCheck
        db.commit()
        db.refresh(result)
        return result
    finally:
        if db.in_transaction():
            db.rollback()
        if release_claim(db, endpoint.id, token):
            db.commit()


def list_check_results(
    db: Session, endpoint_id: int, limit: int
) -> list[CheckResult]:
    statement = (
        select(CheckResult)
        .where(CheckResult.endpoint_id == endpoint_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(limit)
    )
    return list(db.scalars(statement).all())


def get_status_summary(
    db: Session, endpoint_id: int
) -> EndpointStatusResponse:
    latest = db.scalars(
        select(CheckResult)
        .where(CheckResult.endpoint_id == endpoint_id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc())
        .limit(1)
    ).first()

    measured = or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured")
    total_checks = (
        db.scalar(
            select(func.count())
            .select_from(CheckResult)
            .where(CheckResult.endpoint_id == endpoint_id, measured)
        )
        or 0
    )
    successful_checks = (
        db.scalar(
            select(func.count())
            .select_from(CheckResult)
            .where(
                CheckResult.endpoint_id == endpoint_id,
                CheckResult.is_success.is_(True),
            )
        )
        or 0
    )

    endpoint = db.get(Endpoint, endpoint_id)
    agent = db.get(PrivateAgent, endpoint.agent_id) if endpoint.agent_id is not None else None
    return summarize(endpoint, latest, agent, total_checks, successful_checks, _utcnow())


def summarize(endpoint, latest, agent, total_checks, successful_checks, now):
    grace = (endpoint.heartbeat_grace_seconds if endpoint.kind == "heartbeat" else endpoint.timeout_seconds)
    stale = bool(latest and endpoint.is_active and now - latest.checked_at >
                 timedelta(seconds=endpoint.interval_seconds + grace + 2 * settings.SCHEDULER_POLL_SECONDS))
    offline = endpoint.kind == "agent" and not agent_is_online(agent, now)
    known = latest and not stale and not offline and latest.failure_kind != "unconfigured"
    return EndpointStatusResponse(
        endpoint_id=endpoint.id,
        is_up=latest.is_success if known else None,
        agent_offline=offline,
        is_stale=stale,
        certificate_expires_at=latest.certificate_expires_at if latest else None,
        is_slow=bool(known and latest.is_slow),
        last_checked_at=latest.checked_at if latest else None,
        last_status_code=latest.status_code if latest else None,
        last_response_time_ms=latest.response_time_ms if latest else None,
        last_error=latest.error if latest else None,
        total_checks=total_checks,
        successful_checks=successful_checks,
        uptime_percentage=(
            round(successful_checks / total_checks * 100, 2)
            if total_checks
            else None
        ),
    )


def _is_due(
    last_checked_at: datetime | None,
    interval_seconds: int,
    now: datetime,
) -> bool:
    if last_checked_at is None:
        return True
    return now - last_checked_at >= timedelta(seconds=interval_seconds)


async def run_due_checks(
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    """Check every active endpoint whose interval has elapsed."""
    now = _utcnow()

    with session_factory() as db:
        rows = db.execute(select(Endpoint, latest_check_at().label("latest")).where(
            Endpoint.is_active.is_(True), Endpoint.kind != "agent",
        ).order_by(Endpoint.id)).all()
        endpoints = [endpoint for endpoint, _ in rows]
        last_checks = {endpoint.id: _as_utc(latest) for endpoint, latest in rows}
        due = [
            endpoint
            for endpoint in endpoints
            if _is_due(
                (last_checks.get(endpoint.id) or endpoint.created_at) if endpoint.kind == "heartbeat"
                else last_checks.get(endpoint.id),
                endpoint.interval_seconds + (endpoint.heartbeat_grace_seconds or 0),
                now,
            )
        ]

    async def run_claimed(endpoint, token, client):
        try:
            result = await check_endpoint(endpoint, client)
            with session_factory() as db:
                if release_claim(db, endpoint.id, token):
                    saved = save_check_results(db, [result])
                    db.commit()
                    return saved
        except Exception as error:
            logger.error("Health check failed for endpoint %s (%s)", endpoint.id, type(error).__name__)
        finally:
            with session_factory() as db:
                if release_claim(db, endpoint.id, token):
                    db.commit()
        return 0

    if not due:
        return 0
    # Prioritize the oldest observations when there is more work than capacity.
    due.sort(key=lambda endpoint: (last_checks.get(endpoint.id) or datetime.min.replace(tzinfo=timezone.utc), endpoint.id))
    claims = []
    with session_factory() as db:
        for endpoint in due:
            token = claim_check(db, endpoint, now, automatic=True)
            if token is not None:
                claims.append((endpoint, token))
            if len(claims) == MAX_WORKERS_PER_POLL:
                break

    async with create_http_client() as client:
        return sum(await asyncio.gather(*(run_claimed(endpoint, token, client) for endpoint, token in claims)))


def prune_check_history(
    retention_days: int,
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    """Delete check results older than the retention window."""
    now = _utcnow()
    removed = 0
    with session_factory() as db:
        policies = set(db.scalars(select(Endpoint.retention_days).distinct()))
        for policy in policies:
            days = retention_days if policy is None else policy
            if days <= 0:
                continue
            target = select(Endpoint.id).where(Endpoint.retention_days == policy)
            result = db.execute(delete(CheckResult).where(
                CheckResult.endpoint_id.in_(target), CheckResult.checked_at < now - timedelta(days=days),
            ))
            removed += max(0, result.rowcount)
        db.commit()
    return removed



async def monitoring_loop(
    poll_seconds: float,
    retention_days: int = 0,
    cleanup_interval_seconds: int = 3600,
) -> None:
    """Run due checks and prune old results until the app shuts down."""
    logger.info("Endpoint monitoring started")
    last_cleanup: float | None = None
    worker_id, started_at = secrets.token_hex(16), _utcnow()

    while True:
        worker_state, checked = "healthy", 0
        try:
            checked = await run_due_checks()
            if checked:
                logger.info("Stored %s health check result(s)", checked)

            now = time.monotonic()
            if (
                last_cleanup is None
                or now - last_cleanup >= cleanup_interval_seconds
            ):
                # Advance first so a failed prune is not retried every poll.
                last_cleanup = now
                removed = prune_check_history(retention_days)
                if removed:
                    logger.info(
                        "Pruned %s health check result(s) using configured policies",
                        removed,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            worker_state = "error"
            logger.exception("Unexpected error while running health checks")
        try:
            record_poll(worker_id, started_at, worker_state, checked)
        except Exception:
            logger.warning("Could not record scheduler health")
        await asyncio.sleep(poll_seconds)


def range_metrics(db: Session, endpoint_id: int, hours: int) -> dict:
    now = _utcnow()
    start = now - timedelta(hours=hours)
    scope = (CheckResult.endpoint_id == endpoint_id, CheckResult.checked_at >= start,
             CheckResult.checked_at <= now,
             or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured"))
    total, successful, slow = db.execute(select(
        func.count(), func.count().filter(CheckResult.is_success.is_(True)),
        func.count().filter(CheckResult.is_slow.is_(True)),
    ).where(*scope)).one()
    baseline = select(CheckResult.response_time_ms).where(
        *scope, CheckResult.status_code.is_not(None), CheckResult.response_time_ms.is_not(None),
    )
    timed = db.scalar(select(func.count()).select_from(baseline.subquery()))
    percentiles = {}
    for percentile in (50, 95, 99):
        offset = (percentile * timed + 99) // 100 - 1
        percentiles[f"p{percentile}_ms"] = db.scalar(
            baseline.order_by(CheckResult.response_time_ms).offset(offset).limit(1)
        ) if timed else None
    return {
        "starts_at": start, "ends_at": now, "total_checks": total, "successful_checks": successful,
        "slow_checks": slow, "uptime_percentage": round(successful * 100 / total, 4) if total else None,
        **percentiles,
    }


def reliability(db: Session, endpoint: Endpoint) -> dict:
    from decimal import Decimal

    from app.db.models import MaintenanceWindow

    metrics = range_metrics(db, endpoint.id, endpoint.slo_window_hours)
    in_maintenance = select(MaintenanceWindow.id).where(
        MaintenanceWindow.endpoint_id == endpoint.id,
        MaintenanceWindow.starts_at <= CheckResult.checked_at,
        MaintenanceWindow.ends_at > CheckResult.checked_at,
        or_(MaintenanceWindow.canceled_at.is_(None), MaintenanceWindow.canceled_at > CheckResult.checked_at),
    ).correlate(CheckResult).exists()
    maintenance_checks = db.scalar(select(func.count()).select_from(CheckResult).where(
        CheckResult.endpoint_id == endpoint.id,
        CheckResult.checked_at.between(metrics["starts_at"], metrics["ends_at"]),
        or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured"),
        in_maintenance,
    ))
    total = metrics["total_checks"]
    failed = total - metrics["successful_checks"]
    target = endpoint.slo_target_percentage
    allowed = remaining = met = None
    if total and target is not None:
        allowed = total * (Decimal(100) - Decimal(str(target))) / 100
        remaining = allowed - failed
        met = remaining >= 0
    return {
        **metrics, "target_percentage": target, "window_hours": endpoint.slo_window_hours,
        "failed_checks": failed, "maintenance_checks": maintenance_checks,
        "allowed_failures": allowed, "remaining_error_budget": remaining, "target_met": met,
        "missing_check_policy": "excluded", "maintenance_policy": "included",
    }


def latest_check_id():
    return select(CheckResult.id).where(CheckResult.endpoint_id == Endpoint.id).order_by(
        CheckResult.checked_at.desc(), CheckResult.id.desc(),
    ).limit(1).correlate(Endpoint).scalar_subquery()


def dashboard_statuses(db: Session, owner_id: int | None, *, public_only: bool = False):
    from sqlalchemy.orm import aliased

    endpoint_scope = [Endpoint.owner_id == owner_id]
    if public_only:
        endpoint_scope.append(Endpoint.public_label.is_not(None))
    latest = aliased(CheckResult)
    base = select(Endpoint, latest, PrivateAgent).select_from(Endpoint).outerjoin(
        latest, latest.id == latest_check_id(),
    ).outerjoin(PrivateAgent, PrivateAgent.id == Endpoint.agent_id).where(*endpoint_scope).order_by(Endpoint.id)
    if public_only:
        base = base.limit(100)
    rows = list(db.execute(base))
    totals = {}
    if rows and not public_only:
        totals = {
            endpoint_id: (total, successes)
            for endpoint_id, total, successes in db.execute(select(
                CheckResult.endpoint_id, func.count(),
                func.count().filter(CheckResult.is_success.is_(True)),
            ).join(Endpoint, Endpoint.id == CheckResult.endpoint_id).where(
                *endpoint_scope, or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured"),
            ).group_by(CheckResult.endpoint_id))
        }
    now = _utcnow()
    return [
        (endpoint, summarize(endpoint, check, agent, *totals.get(endpoint.id, (0, 0)), now))
        for endpoint, check, agent in rows
    ]



def workspace_trends(db: Session, owner_id: int | None, now: datetime) -> list[dict]:
    width = timedelta(hours=4)
    start = now - 6 * width
    bucket = case(
        *((CheckResult.checked_at < start + (offset + 1) * width, offset) for offset in range(5)),
        else_=5,
    ).label("bucket")
    rows = {
        index: (total, successful, latency)
        for index, total, successful, latency in db.execute(select(
            bucket, func.count(), func.count().filter(CheckResult.is_success.is_(True)),
            func.avg(CheckResult.response_time_ms).filter(CheckResult.status_code.is_not(None)),
        ).join(Endpoint, Endpoint.id == CheckResult.endpoint_id).where(
            Endpoint.owner_id == owner_id, CheckResult.checked_at.between(start, now),
            or_(CheckResult.failure_kind.is_(None), CheckResult.failure_kind != "unconfigured"),
        ).group_by(bucket))
    }
    result = []
    for index in range(6):
        total, successful, latency = rows.get(index, (0, 0, None))
        result.append({
            "starts_at": start + index * width, "total_checks": total,
            "availability": round(successful / total * 100, 2) if total else None,
            "average_latency_ms": round(latency) if latency is not None else None,
        })
    return result


def recent_check_series(db: Session, endpoint_ids: list[int], now: datetime) -> dict[int, list[dict]]:
    """Return up to 24 recent observations per monitor, most recent last."""
    if not endpoint_ids:
        return {}
    ranked = select(
        CheckResult.endpoint_id, CheckResult.checked_at, CheckResult.is_success, CheckResult.response_time_ms,
        CheckResult.failure_kind, CheckResult.id,
        func.row_number().over(
            partition_by=CheckResult.endpoint_id,
            order_by=(CheckResult.checked_at.desc(), CheckResult.id.desc()),
        ).label("position"),
    ).where(CheckResult.endpoint_id.in_(endpoint_ids), CheckResult.checked_at >= now - timedelta(hours=24)).subquery()
    series = {}
    for row in db.execute(select(ranked).where(ranked.c.position <= 24).order_by(
        ranked.c.endpoint_id, ranked.c.position.desc(),
    )):
        known = row.failure_kind != "unconfigured"
        series.setdefault(row.endpoint_id, []).append({
            "at": row.checked_at,
            "ok": row.is_success if known else None,
            "latency_ms": row.response_time_ms if known else None,
        })
    return series
