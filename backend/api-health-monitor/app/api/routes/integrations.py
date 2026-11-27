from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.routes.endpoints import WorkspaceOwner, _get_endpoint_or_404
from app.api.schemas import MonitorURL, Severity
from app.core.auth import authorize_endpoint
from app.db.database import get_db
from app.db.models import AlertDelivery, AlertIntegration, AuditEvent, User
from app.services.audit import audit
from app.services.request_config import monitor_cipher
from app.services.routing import routed_delivery

router = APIRouter(prefix="/integrations", tags=["Alert routing"])


class RouteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    endpoint_id: int | None = None
    kind: Literal["webhook", "slack", "discord"] = "webhook"
    destination: MonitorURL
    service_group: str | None = Field(default=None, min_length=1, max_length=80)
    tag: str | None = Field(default=None, min_length=1, max_length=40)
    severity: Severity | None = None
    escalation_seconds: int | None = Field(default=None, ge=10, le=86400)


class RouteUpdate(BaseModel):
    enabled: bool
    service_group: str | None = Field(default=None, min_length=1, max_length=80)
    tag: str | None = Field(default=None, min_length=1, max_length=40)
    severity: Severity | None = None
    endpoint_id: int | None = None
    escalation_seconds: int | None = Field(default=None, ge=10, le=86400)


class TestRoute(BaseModel):
    endpoint_id: int


def integration_record(db: Session, route: AlertIntegration):
    delivery = db.scalar(
        select(AlertDelivery).where(AlertDelivery.integration_id == route.id)
        .order_by(AlertDelivery.id.desc()).limit(1)
    )
    return {
        "id": route.id, "name": route.name, "kind": route.kind, "enabled": route.enabled,
        "service_group": route.service_group, "tag": route.tag, "severity": route.severity,
        "endpoint_id": route.endpoint_id,
        "escalation_seconds": route.escalation_seconds,
        "last_delivery": {
            "state": delivery.state, "attempts": delivery.attempts, "event": delivery.payload["event"],
            "last_attempt_at": delivery.last_attempt_at, "last_error": delivery.last_error,
        } if delivery else None,
    }


def owned_route(db: Session, route_id: int, owner_id: int | None):
    route = db.scalar(select(AlertIntegration).where(
        AlertIntegration.id == route_id, AlertIntegration.owner_id == owner_id,
    ))
    if route is None:
        raise HTTPException(404, "Alert route not found")
    return route


@router.get("")
def list_integrations(owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    routes = db.scalars(select(AlertIntegration).where(
        AlertIntegration.owner_id == owner_id,
    ).order_by(AlertIntegration.id).limit(100)).all()
    return [integration_record(db, route) for route in routes]


@router.post("", status_code=201)
def create_integration(
    payload: RouteCreate, owner_id: WorkspaceOwner, actor: User = Depends(authorize_endpoint),
    db: Session = Depends(get_db),
):
    if payload.endpoint_id is not None:
        _get_endpoint_or_404(db, payload.endpoint_id, owner_id)
    try:
        encrypted = monitor_cipher().encrypt(str(payload.destination).encode()).decode()
    except ValueError:
        raise HTTPException(503, "Configure the encryption key to save an alert route") from None
    row = AlertIntegration(owner_id=owner_id, destination=encrypted, **payload.model_dump(exclude={"destination"}))
    db.add(row)
    db.flush()
    audit(db, actor.username, owner_id, "alert_route.created", row.id)
    db.commit()
    return integration_record(db, row)


@router.patch("/{route_id}")
def update_integration(
    route_id: int, payload: RouteUpdate, owner_id: WorkspaceOwner, actor: User = Depends(authorize_endpoint),
    db: Session = Depends(get_db),
):
    route = owned_route(db, route_id, owner_id)
    if payload.endpoint_id is not None:
        _get_endpoint_or_404(db, payload.endpoint_id, owner_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(route, field, value)
    audit(db, actor.username, owner_id, "alert_route.updated", route.id)
    db.commit()
    return integration_record(db, route)


@router.post("/{route_id}/test", status_code=202)
def test_integration(
    route_id: int, payload: TestRoute, owner_id: WorkspaceOwner, actor: User = Depends(authorize_endpoint),
    db: Session = Depends(get_db),
):
    route = owned_route(db, route_id, owner_id)
    endpoint = _get_endpoint_or_404(db, payload.endpoint_id, owner_id)
    if not route.enabled:
        raise HTTPException(409, "Enable this route before testing it")
    db.execute(update(AlertIntegration).where(
        AlertIntegration.id == route.id,
    ).values(enabled=AlertIntegration.enabled))
    now = datetime.now(timezone.utc)
    recent = db.scalar(select(AlertDelivery.id).where(
        AlertDelivery.integration_id == route.id, AlertDelivery.created_at >= now - timedelta(seconds=30),
        AlertDelivery.payload["event"].as_string() == "integration.test",
    ).limit(1))
    if recent:
        raise HTTPException(429, "Wait 30 seconds between test notifications")
    row = routed_delivery(endpoint, None, route, "integration.test", {
        "endpoint_id": endpoint.id, "endpoint_name": endpoint.name, "checked_at": now.isoformat(),
    }, now)
    db.add(row)
    audit(db, actor.username, owner_id, "alert_route.test", route_id)
    db.commit()
    return {"id": row.id, "state": row.state}


@router.get("/audit")
def list_audit_events(
    owner_id: WorkspaceOwner, limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return [{
        "id": event.id, "actor": event.actor, "action": event.action,
        "subject_id": event.subject_id, "created_at": event.created_at,
    } for event in db.scalars(select(AuditEvent).where(AuditEvent.owner_id == owner_id)
                              .order_by(AuditEvent.id.desc()).limit(limit))]
