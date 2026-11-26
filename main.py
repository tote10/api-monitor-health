import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.endpoints import router as endpoints_router
from app.core.auth import require_user, router as auth_router, session_user
from app.core.config import settings
from app.db.database import engine, get_db
from app.services.alert_service import alert_loop, enabled_channels
from app.services.monitor_service import monitoring_loop

APP_DIR = Path(__file__).resolve().parent / "app"


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = []
    if settings.MONITORING_ENABLED:
        scheduler = asyncio.create_task(
            monitoring_loop(
                settings.SCHEDULER_POLL_SECONDS,
                settings.CHECK_HISTORY_RETENTION_DAYS,
                settings.CHECK_HISTORY_CLEANUP_INTERVAL_SECONDS,
            )
        )
        tasks.append(scheduler)
    if enabled_channels():
        tasks.append(asyncio.create_task(alert_loop(settings.SCHEDULER_POLL_SECONDS)))

    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def root(request: Request, db: Session = Depends(get_db)):
    """Serve the dashboard to browsers and status JSON to API clients."""
    if "text/html" in request.headers.get("accept", ""):
        if session_user(request, db) is None:
            return RedirectResponse("/login", headers={"Cache-Control": "no-store"})
        return FileResponse(APP_DIR / "templates" / "index.html", headers={"Cache-Control": "no-store"})

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


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(APP_DIR / "templates" / "login.html")


@app.get("/register", include_in_schema=False)
def registration_page():
    return FileResponse(APP_DIR / "templates" / "register.html", headers={"Cache-Control": "no-store"})


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_user)])
def docs():
    return get_swagger_ui_html(openapi_url="/openapi.json", title=app.title)


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_user)])
def redoc():
    return get_redoc_html(openapi_url="/openapi.json", title=app.title)


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_user)])
def openapi():
    return app.openapi()


app.include_router(auth_router)
app.include_router(endpoints_router, prefix=settings.API_V1_PREFIX)
