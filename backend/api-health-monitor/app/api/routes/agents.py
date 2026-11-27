import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.routes.endpoints import WorkspaceOwner
from app.api.schemas import EndpointResponse, StatusCode
from app.core.auth import authorize_endpoint, token_hash
from app.db.database import get_db
from app.db.models import CheckResult, Endpoint, PrivateAgent, User
from app.services.agent_health import agent_is_online
from app.services.alert_service import save_check_results
from app.services.audit import audit
from app.services.check_claims import claim_check, latest_check_at, release_claim

router = APIRouter(prefix="/agents", tags=["Private agents"])
AgentAuthorization = Annotated[str | None, Header()]


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PrivateMonitorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
    interval_seconds: int = Field(default=60, ge=10, le=3600)
    timeout_seconds: int = Field(default=5, ge=1, le=60)
    expected_status_code: StatusCode = 200


class AgentResult(BaseModel):
    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    status_code: StatusCode | None = None
    response_time_ms: int | None = Field(default=None, ge=0, le=120000)
    failure: Literal["timeout", "connectivity", "unconfigured"] | None = None

    @model_validator(mode="after")
    def validate_result(self):
        if (self.status_code is None) == (self.failure is None):
            raise ValueError("Provide either an HTTP status code or a failure classification")
        return self


def agent_record(agent: PrivateAgent) -> dict:
    return {
        "id": agent.id, "name": agent.name, "online": agent_is_online(agent, datetime.now(timezone.utc)),
        "last_seen_at": agent.last_seen_at, "credential_configured": bool(agent.token_hash),
    }


def owned_agent(db: Session, agent_id: int, owner_id: int | None) -> PrivateAgent:
    agent = db.scalar(select(PrivateAgent).where(
        PrivateAgent.id == agent_id, PrivateAgent.owner_id == owner_id,
    ))
    if agent is None:
        raise HTTPException(404, "Agent not found")
    return agent


def authenticate_agent(db: Session, agent_id: int, authorization: str | None) -> PrivateAgent:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or len(token) != 43:
        raise HTTPException(401, "Valid agent credential required")
    changed = db.execute(update(PrivateAgent).where(
        PrivateAgent.id == agent_id, PrivateAgent.token_hash == token_hash(token),
    ).values(last_seen_at=datetime.now(timezone.utc))).rowcount
    if not changed:
        raise HTTPException(401, "Valid agent credential required")
    return db.get(PrivateAgent, agent_id)


@router.get("")
def list_agents(owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    agents = db.scalars(select(PrivateAgent).where(PrivateAgent.owner_id == owner_id).order_by(PrivateAgent.name))
    return [agent_record(agent) for agent in agents]


@router.post("", status_code=201)
def register_agent(
    payload: AgentCreate, response: Response, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    token = secrets.token_urlsafe(32)
    agent = PrivateAgent(name=payload.name, owner_id=owner_id, token_hash=token_hash(token))
    db.add(agent)
    db.flush()
    audit(db, actor.username, owner_id, "agent.registered", agent.id)
    db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return {**agent_record(agent), "token": token}


@router.post("/{agent_id}/rotate")
def rotate_agent(
    agent_id: int, response: Response, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    agent = owned_agent(db, agent_id, owner_id)
    token = secrets.token_urlsafe(32)
    agent.token_hash, agent.last_seen_at = token_hash(token), None
    audit(db, actor.username, owner_id, "agent.credential_rotated", agent.id)
    db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return {**agent_record(agent), "token": token}


@router.post("/{agent_id}/revoke", status_code=204)
def revoke_agent(
    agent_id: int, owner_id: WorkspaceOwner, actor: User = Depends(authorize_endpoint),
    db: Session = Depends(get_db),
):
    agent = owned_agent(db, agent_id, owner_id)
    agent.token_hash = None
    audit(db, actor.username, owner_id, "agent.credential_revoked", agent.id)
    db.commit()
    return Response(status_code=204)


@router.post("/{agent_id}/monitors", status_code=201, response_model=EndpointResponse)
def add_private_monitor(
    agent_id: int, payload: PrivateMonitorCreate, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    agent = owned_agent(db, agent_id, owner_id)
    monitor = Endpoint(
        **payload.model_dump(exclude={"target"}), kind="agent", agent_id=agent.id,
        agent_target=payload.target, owner_id=owner_id, url="https://agent.invalid/" + secrets.token_hex(16),
    )
    db.add(monitor)
    db.flush()
    audit(db, actor.username, owner_id, "monitor.private_created", monitor.id)
    db.commit()
    return monitor


@router.post("/{agent_id}/poll")
def agent_poll(agent_id: int, authorization: AgentAuthorization = None, db: Session = Depends(get_db)):
    agent = authenticate_agent(db, agent_id, authorization)
    now = datetime.now(timezone.utc)
    recent = latest_check_at()
    candidates = db.execute(select(Endpoint, recent.label("latest")).where(
        Endpoint.agent_id == agent.id, Endpoint.owner_id == agent.owner_id,
        Endpoint.kind == "agent", Endpoint.is_active.is_(True),
    ).order_by(recent.asc().nulls_first(), Endpoint.id)).all()
    db.commit()
    jobs = []
    for endpoint, last_checked_at in candidates:
        if last_checked_at and now - last_checked_at < timedelta(seconds=endpoint.interval_seconds):
            continue
        token = claim_check(db, endpoint, now, automatic=True)
        if token:
            jobs.append({"monitor_id": endpoint.id, "target": endpoint.agent_target,
                         "timeout_seconds": endpoint.timeout_seconds, "token": token})
        if len(jobs) == 4:
            break
    return jobs


@router.post("/{agent_id}/results/{monitor_id}", status_code=204)
def agent_result(
    agent_id: int, monitor_id: int, payload: AgentResult,
    authorization: AgentAuthorization = None, db: Session = Depends(get_db),
):
    agent = authenticate_agent(db, agent_id, authorization)
    endpoint = db.scalar(select(Endpoint).where(
        Endpoint.id == monitor_id, Endpoint.agent_id == agent.id,
        Endpoint.owner_id == agent.owner_id, Endpoint.kind == "agent", Endpoint.is_active.is_(True),
    ))
    now = datetime.now(timezone.utc)
    if endpoint is None or not endpoint.check_lease_until or endpoint.check_lease_until <= now or not release_claim(db, monitor_id, payload.token):
        raise HTTPException(409, "Check lease is no longer active")
    kind = payload.failure
    error = {"timeout": "Private check timed out", "connectivity": "Private request failed",
             "unconfigured": "Target is missing from the agent configuration"}.get(kind)
    if payload.status_code is not None and payload.status_code not in (endpoint.expected_status_codes or [endpoint.expected_status_code]):
        kind = "http_status"
        error = f"Unexpected status {payload.status_code}"
    if kind == "unconfigured":
        # A configuration error is shown as unknown and does not open an API outage.
        db.add(CheckResult(endpoint_id=monitor_id, checked_at=now, is_success=False, failure_kind=kind, error=error))
    else:
        save_check_results(db, [CheckResult(
            endpoint_id=monitor_id, checked_at=now, is_success=error is None, status_code=payload.status_code,
            response_time_ms=payload.response_time_ms, failure_kind=kind, error=error,
            is_slow=bool(error is None and endpoint.latency_budget_ms and payload.response_time_ms is not None
                         and payload.response_time_ms > endpoint.latency_budget_ms),
        )])
    db.commit()
    return Response(status_code=204)
