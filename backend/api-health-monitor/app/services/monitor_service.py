import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import EndpointStatusResponse
from app.db.database import SessionLocal
from app.db.models import CheckResult, Endpoint

logger = logging.getLogger(__name__)


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
    return httpx.AsyncClient(follow_redirects=True)


async def check_endpoint(
    endpoint: Endpoint, client: httpx.AsyncClient
) -> CheckResult:
    """Call an endpoint once and build an unsaved check result."""
    started = time.perf_counter()
    status_code = None
    error = None

    try:
        response = await client.get(
            endpoint.url,
            timeout=endpoint.timeout_seconds,
        )
        status_code = response.status_code
        if status_code != endpoint.expected_status_code:
            error = (
                f"Expected status {endpoint.expected_status_code}, "
                f"got {status_code}"
            )
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"

    response_time_ms = max(0, round((time.perf_counter() - started) * 1000))

    return CheckResult(
        endpoint_id=endpoint.id,
        checked_at=_utcnow(),
        is_success=error is None,
        status_code=status_code,
        response_time_ms=response_time_ms,
        error=error[:500] if error else None,
    )


async def perform_check(db: Session, endpoint: Endpoint) -> CheckResult:
    """Run a check immediately and persist the result."""
    async with create_http_client() as client:
        result = await check_endpoint(endpoint, client)

    db.add(result)
    db.commit()
    db.refresh(result)
    return result


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

    total_checks = (
        db.scalar(
            select(func.count())
            .select_from(CheckResult)
            .where(CheckResult.endpoint_id == endpoint_id)
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

    return EndpointStatusResponse(
        endpoint_id=endpoint_id,
        is_up=latest.is_success if latest else None,
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
        endpoints = list(
            db.scalars(
                select(Endpoint)
                .where(Endpoint.is_active.is_(True))
                .order_by(Endpoint.id)
            ).all()
        )
        last_checks = {
            endpoint_id: _as_utc(checked_at)
            for endpoint_id, checked_at in db.execute(
                select(
                    CheckResult.endpoint_id,
                    func.max(CheckResult.checked_at),
                ).group_by(CheckResult.endpoint_id)
            ).all()
        }
        due = [
            endpoint
            for endpoint in endpoints
            if _is_due(
                last_checks.get(endpoint.id),
                endpoint.interval_seconds,
                now,
            )
        ]

    if not due:
        return 0

    async with create_http_client() as client:
        outcomes = await asyncio.gather(
            *(check_endpoint(endpoint, client) for endpoint in due),
            return_exceptions=True,
        )

    results = []
    for endpoint, outcome in zip(due, outcomes):
        if isinstance(outcome, Exception):
            logger.error(
                "Health check crashed for endpoint %s",
                endpoint.id,
                exc_info=outcome,
            )
            continue
        results.append(outcome)

    if not results:
        return 0

    with session_factory() as db:
        for result in results:
            db.add(result)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            logger.exception("Could not store health check results")
            return 0

    return len(results)


def prune_check_history(
    retention_days: int,
    session_factory: Callable[[], Session] = SessionLocal,
) -> int:
    """Delete check results older than the retention window."""
    if retention_days <= 0:
        return 0

    cutoff = _utcnow() - timedelta(days=retention_days)

    with session_factory() as db:
        result = db.execute(
            delete(CheckResult).where(CheckResult.checked_at < cutoff)
        )
        db.commit()
        removed = result.rowcount
        return removed if removed and removed > 0 else 0


async def monitoring_loop(
    poll_seconds: float,
    retention_days: int = 0,
    cleanup_interval_seconds: int = 3600,
) -> None:
    """Run due checks and prune old results until the app shuts down."""
    logger.info("Endpoint monitoring started")
    last_cleanup: float | None = None

    while True:
        try:
            checked = await run_due_checks()
            if checked:
                logger.info("Stored %s health check result(s)", checked)

            now = time.monotonic()
            if retention_days > 0 and (
                last_cleanup is None
                or now - last_cleanup >= cleanup_interval_seconds
            ):
                # Advance first so a failed prune is not retried every poll.
                last_cleanup = now
                removed = prune_check_history(retention_days)
                if removed:
                    logger.info(
                        "Pruned %s health check result(s) older than %s day(s)",
                        removed,
                        retention_days,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected error while running health checks")
        await asyncio.sleep(poll_seconds)
