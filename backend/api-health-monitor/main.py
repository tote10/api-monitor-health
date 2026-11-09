import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.routes.endpoints import router as endpoints_router
from app.core.config import settings
from app.services.monitor_service import monitoring_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    monitor_task = None
    if settings.MONITORING_ENABLED:
        monitor_task = asyncio.create_task(
            monitoring_loop(settings.SCHEDULER_POLL_SECONDS)
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


@app.get("/")
def root():
    return {"status": "running", "docs": "/docs"}


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
    }


app.include_router(endpoints_router, prefix=settings.API_V1_PREFIX)
