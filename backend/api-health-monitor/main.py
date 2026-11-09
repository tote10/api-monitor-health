from fastapi import FastAPI

from app.api.routes.endpoints import router as endpoints_router
from app.core.config import settings


app = FastAPI(title=settings.PROJECT_NAME, version=settings.VERSION)


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
