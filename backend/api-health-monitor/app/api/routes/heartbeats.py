import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.routes.endpoints import WorkspaceOwner, _get_endpoint_or_404
from app.api.schemas import EndpointResponse
from app.core.auth import authorize_endpoint, token_hash
from app.db.database import get_db
from app.db.models import CheckResult, Endpoint, User
from app.services.alert_service import save_check_results
from app.services.audit import audit

router = APIRouter(prefix="/heartbeats", tags=["Heartbeats"])


class HeartbeatSettings(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    interval_seconds: int = Field(ge=10, le=2592000)
    heartbeat_grace_seconds: int = Field(ge=0, le=3600)


class HeartbeatCredential(BaseModel):
    endpoint: EndpointResponse
    token: str


def get_heartbeat(db: Session, monitor_id: int, owner_id: int | None) -> Endpoint:
    endpoint = _get_endpoint_or_404(db, monitor_id, owner_id)
    if endpoint.kind != "heartbeat":
        raise HTTPException(404, "Heartbeat monitor not found")
    return endpoint


@router.post("", status_code=201, response_model=HeartbeatCredential)
def create_heartbeat(
    payload: HeartbeatSettings, response: Response, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    token = secrets.token_urlsafe(32)
    endpoint = Endpoint(
        **payload.model_dump(), kind="heartbeat", owner_id=owner_id,
        url="https://heartbeat.invalid/" + secrets.token_hex(16),
        heartbeat_token_hash=token_hash(token),
    )
    db.add(endpoint)
    db.flush()
    audit(db, actor.username, owner_id, "monitor.heartbeat_created", endpoint.id)
    db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return {"endpoint": endpoint, "token": token}


@router.patch("/{monitor_id}", response_model=EndpointResponse)
def configure_heartbeat(
    monitor_id: int, payload: HeartbeatSettings, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    endpoint = get_heartbeat(db, monitor_id, owner_id)
    for field, value in payload.model_dump().items():
        setattr(endpoint, field, value)
    # Reject a missed check that was claimed against the old schedule.
    db.execute(update(Endpoint).where(Endpoint.id == endpoint.id).values(
        check_epoch=Endpoint.check_epoch + 1, check_token=None, check_lease_until=None,
    ))
    audit(db, actor.username, owner_id, "monitor.heartbeat_updated", endpoint.id)
    db.commit()
    return endpoint


@router.post("/{monitor_id}/rotate", response_model=HeartbeatCredential)
def rotate_heartbeat(
    monitor_id: int, response: Response, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    endpoint = get_heartbeat(db, monitor_id, owner_id)
    token = secrets.token_urlsafe(32)
    endpoint.heartbeat_token_hash = token_hash(token)
    audit(db, actor.username, owner_id, "heartbeat.credential_rotated", endpoint.id)
    db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return {"endpoint": endpoint, "token": token}


@router.post("/{monitor_id}/revoke", status_code=204)
def revoke_heartbeat(
    monitor_id: int, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    endpoint = get_heartbeat(db, monitor_id, owner_id)
    endpoint.heartbeat_token_hash = None
    audit(db, actor.username, owner_id, "heartbeat.credential_revoked", endpoint.id)
    db.commit()
    return Response(status_code=204)


@router.post("/{monitor_id}/ping", status_code=204)
def submit_heartbeat(
    monitor_id: int, authorization: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or len(token) != 43:
        raise HTTPException(401, "Valid heartbeat credential required")
    changed = db.execute(update(Endpoint).where(
        Endpoint.id == monitor_id, Endpoint.kind == "heartbeat", Endpoint.heartbeat_token_hash == token_hash(token),
    ).values(
        check_epoch=Endpoint.check_epoch + 1, check_token=None, check_lease_until=None,
    )).rowcount
    if not changed:
        raise HTTPException(401, "Valid heartbeat credential required")
    now = datetime.now(timezone.utc)
    db.execute(update(Endpoint).where(Endpoint.id == monitor_id).values(last_heartbeat_at=now))
    save_check_results(db, [CheckResult(endpoint_id=monitor_id, is_success=True, checked_at=now)])
    db.commit()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
