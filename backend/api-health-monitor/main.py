import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.endpoints import router as endpoints_router
from app.core.config import settings
from app.db.database import engine
from app.services.monitor_service import monitoring_loop

STATIC_DIR = Path(__file__).resolve().parent / "app" / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    monitor_task = None
    if settings.MONITORING_ENABLED:
        monitor_task = asyncio.create_task(
            monitoring_loop(
                settings.SCHEDULER_POLL_SECONDS,
                settings.CHECK_HISTORY_RETENTION_DAYS,
                settings.CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS,
            )
        )

    try:
        yield
    finally:
        if monitor_task is not None:
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def root(request: Request):
    """Serve the dashboard to browsers and status JSON to API clients."""
    if "text/html" in request.headers.get("accept", ""):
        return FileResponse(STATIC_DIR / "index.html")

    return {
        "status": "running",
        "docs": "/docs",
        "dashboard": "/",
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "monitoring_enabled": settings.MONITORING_ENABLED,
    }


@app.get("/ready")
def readiness_check():
    """Report readiness only when the database is reachable."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "database": "unreachable"},
        )

    return {
        "status": "ready",
        "database": "ok",
        "monitoring_enabled": settings.MONITORING_ENABLED,
    }


app.include_router(endpoints_router, prefix=settings.API_V1_PREFIX)
