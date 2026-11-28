import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.routes.email_settings import AddressInput, check_write_origin, require_email_config
from app.api.routes.endpoints import WorkspaceOwner
from app.core.auth import authorize_endpoint
from app.db.database import get_db
from app.db.models import AlertDelivery, EmailRecipient, Endpoint, Incident, MaintenanceWindow, StatusPage, User
from app.services.audit import audit
from app.services.email_notifications import email_ready, queue_confirmation, status_scope
from app.services.monitor_service import dashboard_statuses
from app.web_pages import page_response

router = APIRouter(tags=["Status page"])


class StatusSettings(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    enabled: bool


def public_settings(page: StatusPage | None) -> dict:
    return {"title": page.title, "enabled": page.enabled, "path": f"/status/{page.slug}"} if page else {
        "title": "Service status", "enabled": False, "path": None,
    }


@router.get("/operations/status-settings")
def get_status_settings(owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    return public_settings(db.get(StatusPage, status_scope(owner_id)))


@router.put("/operations/status-settings")
def update_status_settings(
    payload: StatusSettings, owner_id: WorkspaceOwner, user: User = Depends(authorize_endpoint),
    db: Session = Depends(get_db),
):
    scope = status_scope(owner_id)
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


def status_owner(page: StatusPage) -> int | None:
    return int(page.scope_key.split(":")[1]) if page.scope_key.startswith("user:") else None


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
    owner_id = status_owner(page)
    services = dashboard_statuses(db, owner_id, public_only=True)
    labels = {endpoint.id: endpoint.public_label for endpoint, _ in services}
    now = datetime.now(timezone.utc)
    states = []
    for endpoint, summary in services:
        state = "paused" if not endpoint.is_active else (
            "operational" if summary.is_up is True else "unavailable" if summary.is_up is False else "unknown"
        )
        states.append({"name": endpoint.public_label, "state": state, "id": endpoint.id})
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
        "subscriptions_enabled": email_ready(),
        "incidents": [{
            "service": labels[item.endpoint_id], "message": item.public_update,
            "opened_at": item.opened_at, "resolved_at": item.recovered_at,
        } for item in updates],
        "maintenance": [{
            "service": labels[item.endpoint_id], "starts_at": item.starts_at, "ends_at": item.ends_at,
        } for item in windows],
    }



@router.post("/status/{slug}/services/{endpoint_id}/subscribe", status_code=202,
             dependencies=[Depends(check_write_origin)])
def subscribe_to_service(slug: str, endpoint_id: int, payload: AddressInput, db: Session = Depends(get_db)):
    page = enabled_page(db, slug)
    require_email_config()
    endpoint = db.scalar(select(Endpoint).where(
        Endpoint.id == endpoint_id, Endpoint.owner_id == status_owner(page),
        Endpoint.public_label.is_not(None),
    ).with_for_update())
    if not endpoint:
        raise HTTPException(404, "Published service not found")
    now = datetime.now(timezone.utc)
    scope = f"public:{endpoint.id}"
    count = db.scalar(select(func.count()).select_from(AlertDelivery).where(
        AlertDelivery.endpoint_id == endpoint.id,
        AlertDelivery.payload["event"].as_string() == "email.confirm",
        AlertDelivery.created_at >= now - timedelta(hours=1),
    ))
    if count >= 30:
        raise HTTPException(429, "Too many subscription requests for this service. Try again later.")
    recipient = db.scalar(select(EmailRecipient).where(
        EmailRecipient.scope == scope, EmailRecipient.address == payload.address,
    ))
    if not recipient:
        recipient = EmailRecipient(scope=scope, public_endpoint_id=endpoint.id, address=payload.address)
        db.add(recipient)
    if recipient.unsubscribe_hash is None or recipient.disabled_at:
        recipient.unsubscribe_hash = secrets.token_hex(32)
    try:
        db.flush()
        queue_confirmation(db, recipient, now, endpoint)
        db.commit()
    except IntegrityError:
        db.rollback()
    return {"message": "If confirmation is needed, check your email for a subscription link."}
