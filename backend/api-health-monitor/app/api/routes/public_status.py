import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.endpoints import WorkspaceOwner
from app.core.auth import authorize_endpoint
from app.db.database import get_db
from app.db.models import Incident, MaintenanceWindow, StatusPage, User
from app.services.audit import audit
from app.services.monitor_service import dashboard_statuses
from app.web_pages import page_response

router = APIRouter(tags=["Status page"])


class StatusSettings(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    enabled: bool


def scope_key(owner_id: int | None) -> str:
    return f"user:{owner_id}" if owner_id is not None else "shared"


def public_settings(page: StatusPage | None) -> dict:
    return {"title": page.title, "enabled": page.enabled, "path": f"/status/{page.slug}"} if page else {
        "title": "Service status", "enabled": False, "path": None,
    }


@router.get("/operations/status-settings")
def get_status_settings(owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    return public_settings(db.get(StatusPage, scope_key(owner_id)))


@router.put("/operations/status-settings")
def update_status_settings(
    payload: StatusSettings, owner_id: WorkspaceOwner, user: User = Depends(authorize_endpoint),
    db: Session = Depends(get_db),
):
    scope = scope_key(owner_id)
    page = db.get(StatusPage, scope)
    if not page:
        page = StatusPage(scope_key=scope, slug=secrets.token_urlsafe(18), **payload.model_dump())
        db.add(page)
    else:
        page.title = payload.title
        page.enabled = payload.enabled
    audit(db, user.username, owner_id, "status_page.updated")
    try:
        db.commit()
    except IntegrityError as exc:
        raise HTTPException(409, "Settings changed concurrently. Refresh and try again.") from exc
    return public_settings(page)


def enabled_page(db: Session, slug: str) -> StatusPage:
    page = db.scalar(select(StatusPage).where(StatusPage.slug == slug, StatusPage.enabled.is_(True)))
    if not page:
        raise HTTPException(404, "Status page not found")
    return page


@router.get("/status/{slug}", include_in_schema=False)
def status_page(slug: str, db: Session = Depends(get_db)):
    enabled_page(db, slug)
    return page_response("public_status.html", headers={"X-Robots-Tag": "noindex"})


@router.get("/status/{slug}/snapshot")
def public_snapshot(slug: str, response: Response, db: Session = Depends(get_db)):
    page = enabled_page(db, slug)
    owner_id = int(page.scope_key.split(":")[1]) if page.scope_key.startswith("user:") else None
    services = dashboard_statuses(db, owner_id, public_only=True)
    labels = {endpoint.id: endpoint.public_label for endpoint, _ in services}
    now = datetime.now(timezone.utc)
    states = []
    for endpoint, summary in services:
        state = "paused" if not endpoint.is_active else (
            "operational" if summary.is_up is True else "unavailable" if summary.is_up is False else "unknown"
        )
        states.append({"name": endpoint.public_label, "state": state})
    updates = db.scalars(select(Incident).where(
        Incident.endpoint_id.in_(labels), Incident.public_update.is_not(None),
        or_(Incident.recovered_at.is_(None), Incident.recovered_at >= now - timedelta(days=30)),
    ).order_by(Incident.opened_at.desc()).limit(30))
    windows = db.scalars(select(MaintenanceWindow).where(
        MaintenanceWindow.endpoint_id.in_(labels), MaintenanceWindow.ends_at > now, MaintenanceWindow.canceled_at.is_(None),
    ).order_by(MaintenanceWindow.starts_at).limit(30))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return {
        "title": page.title,
        "services": states,
        "incidents": [{
            "service": labels[item.endpoint_id], "message": item.public_update,
            "opened_at": item.opened_at, "resolved_at": item.recovered_at,
        } for item in updates],
        "maintenance": [{
            "service": labels[item.endpoint_id], "starts_at": item.starts_at, "ends_at": item.ends_at,
        } for item in windows],
    }
