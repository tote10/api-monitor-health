import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes.agents import router as agents_router
from app.api.routes.endpoints import router as endpoints_router
from app.api.routes.heartbeats import router as heartbeats_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.public_status import router as public_status_router
from app.core.auth import require_admin, router as auth_router, session_user
from app.core.config import settings
from app.db.database import engine, get_db
from app.services.alert_service import alert_loop, enabled_channels
from app.services.diagnostics import scheduler_diagnostics
from app.services.monitor_service import monitoring_loop
from app.web_pages import APP_DIR, RevalidatedStaticFiles, page_response


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
    if settings.MONITORING_ENABLED or enabled_channels():
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

@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, error: RequestValidationError):
    # Validation errors must never echo credentials or request options.
    detail = []
    for issue in error.errors():
        location = issue["loc"]
        if "request_options" in location:
            location = location[:location.index("request_options") + 1]
        detail.append({"type": issue["type"], "loc": location, "msg": issue["msg"]})
    return JSONResponse(status_code=422, content={"detail": detail})


app.mount("/static", RevalidatedStaticFiles(directory=APP_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
def root(request: Request, db: Session = Depends(get_db)):
    """Serve the dashboard to browsers and status JSON to API clients."""
    if "text/html" in request.headers.get("accept", ""):
        if session_user(request, db) is None:
            return RedirectResponse("/login", headers={"Cache-Control": "no-store"})
        return page_response("index.html")

    payload = {
        "status": "running",
        "dashboard": "/",
    }
    user = session_user(request, db)
    if user is not None and user.role == "admin":
        payload["docs"] = "/docs"
    return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})


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


@app.get("/operations/diagnostics", dependencies=[Depends(require_admin)])
def diagnostics(db: Session = Depends(get_db)):
    payload = scheduler_diagnostics(db)
    return JSONResponse(jsonable_encoder(payload), headers={"Cache-Control": "private, no-store"})


@app.get("/login", include_in_schema=False)
def login_page():
    return page_response("login.html")


@app.get("/register", include_in_schema=False)
def registration_page():
    return page_response("register.html")


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_admin)])
def docs():
    response = get_swagger_ui_html(openapi_url="/openapi.json", title=app.title)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_admin)])
def redoc():
    response = get_redoc_html(openapi_url="/openapi.json", title=app.title)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/openapi.json", include_in_schema=False, dependencies=[Depends(require_admin)])
def openapi():
    return JSONResponse(app.openapi(), headers={"Cache-Control": "private, no-store"})


app.include_router(auth_router)
app.include_router(endpoints_router, prefix=settings.API_V1_PREFIX)
app.include_router(heartbeats_router, prefix=settings.API_V1_PREFIX)
app.include_router(integrations_router, prefix=settings.API_V1_PREFIX)
app.include_router(public_status_router)
app.include_router(agents_router, prefix=settings.API_V1_PREFIX)
