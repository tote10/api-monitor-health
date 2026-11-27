from datetime import datetime, timedelta, timezone
from typing import Annotated, List, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.schemas import (
    CheckResultResponse,
    DeploymentCreate,
    DeploymentRecord,
    EndpointCreate,
    EndpointIncidentResponse,
    EndpointResponse,
    EndpointStatusResponse,
    EndpointUpdate,
    IncidentNote,
    IncidentRecord,
    IncidentReportUpdate,
    MaintenanceCreate,
    MaintenanceRecord,
)
from app.core.auth import authorize_endpoint
from app.db.database import get_db
from app.db.models import AlertDelivery, CheckResult, DeploymentMarker, Endpoint, Incident, MaintenanceWindow, User
from app.services import endpoint_service, incident_service, monitor_service
from app.services.audit import audit
from app.services.csv_exports import stream_rows
from app.services.endpoint_service import EndpointAlreadyExists
from app.services.request_config import monitor_cipher
from app.services.routing import cancel_escalations

router = APIRouter(prefix="/endpoints", tags=["Endpoints"])


def workspace_owner(user: User = Depends(authorize_endpoint)) -> int | None:
    return user.id if user.role == "member" else None


WorkspaceOwner = Annotated[int | None, Depends(workspace_owner)]


def _get_endpoint_or_404(db: Session, endpoint_id: int, owner_id: int | None) -> Endpoint:
    endpoint = endpoint_service.get_endpoint_by_id(db, endpoint_id, owner_id)
    if endpoint is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint with ID {endpoint_id} not found",
        )
    return endpoint


@router.post(
    "",
    response_model=EndpointResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new monitored endpoint",
)
def create_endpoint(
    payload: EndpointCreate, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    if payload.request_options is not None:
        try:
            monitor_cipher()
        except ValueError:
            raise HTTPException(503, "Request settings encryption is unavailable") from None
    try:
        return endpoint_service.register_endpoint(db, payload, owner_id, actor.username)
    except EndpointAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.get(
    "",
    response_model=List[EndpointResponse],
    summary="List all monitored endpoints",
)
def get_endpoints(owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    return endpoint_service.list_endpoints(db, owner_id)


@router.get("/operations/incidents")
def workspace_incidents(
    owner_id: WorkspaceOwner,
    incident_state: Literal["all", "open", "resolved"] = "open",
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    statement = (
        select(Incident, Endpoint.name)
        .join(Endpoint, Incident.endpoint_id == Endpoint.id)
        .where(Endpoint.owner_id == owner_id)
        .order_by(Incident.opened_at.desc(), Incident.id.desc()).limit(limit)
    )
    if incident_state == "open":
        statement = statement.where(Incident.recovered_at.is_(None))
    elif incident_state == "resolved":
        statement = statement.where(Incident.recovered_at.is_not(None))
    return [
        {"endpoint_name": name, **IncidentRecord.model_validate(incident).model_dump()}
        for incident, name in db.execute(statement)
    ]


@router.get("/operations/overview")
def overview(owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    monitors = monitor_service.dashboard_statuses(db, owner_id)
    now = datetime.now(timezone.utc)
    recent = db.execute(select(CheckResult, Endpoint.name).join(
        Endpoint, Endpoint.id == CheckResult.endpoint_id,
    ).where(
        Endpoint.owner_id == owner_id, CheckResult.is_success.is_(False),
        CheckResult.checked_at >= now - timedelta(hours=24),
    ).order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(8))
    maintenance = db.execute(select(MaintenanceWindow, Endpoint.name).join(
        Endpoint, Endpoint.id == MaintenanceWindow.endpoint_id,
    ).where(
        Endpoint.owner_id == owner_id, MaintenanceWindow.ends_at > now,
        MaintenanceWindow.canceled_at.is_(None),
    ).order_by(MaintenanceWindow.starts_at).limit(20))
    return {
        "trends": monitor_service.workspace_trends(db, owner_id, now),
        "maintenance": [{
            "service": name, "starts_at": window.starts_at, "ends_at": window.ends_at,
            "title": window.title,
        } for window, name in maintenance],
        "monitors": [{
            "endpoint": EndpointResponse.model_validate(endpoint).model_dump(),
            "status": summary.model_dump(),
        } for endpoint, summary in monitors],
        "recent_failures": [{
            "service": name, "checked_at": result.checked_at, "reason": result.error,
            "kind": result.failure_kind, "failed_step": result.failed_step,
        } for result, name in recent],
    }


@router.get(
    "/{endpoint_id}",
    response_model=EndpointResponse,
    summary="Get endpoint details by ID",
)
def get_endpoint(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    return _get_endpoint_or_404(db, endpoint_id, owner_id)


@router.patch(
    "/{endpoint_id}",
    response_model=EndpointResponse,
    summary="Update endpoint configuration",
)
def update_endpoint(
    endpoint_id: int,
    payload: EndpointUpdate,
    owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint),
    db: Session = Depends(get_db),
):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    if endpoint.kind == "heartbeat" and payload.model_fields_set - {"is_active", "slo_target_percentage", "slo_window_hours", "tags", "service_group", "severity", "public_label", "retention_days"}:
        raise HTTPException(422, "Configure this monitor through the heartbeat settings")
    if endpoint.kind == "agent" and payload.model_fields_set & {"url", "request_options", "method"}:
        raise HTTPException(422, "Private target addresses and methods are configured on the agent")
    if payload.request_options is not None:
        try:
            monitor_cipher()
        except ValueError:
            raise HTTPException(503, "Request settings encryption is unavailable") from None
    try:
        return endpoint_service.update_endpoint(db, endpoint, payload, actor.username)
    except EndpointAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/{endpoint_id}/check",
    response_model=CheckResultResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a health check for an endpoint now",
)
async def run_endpoint_check(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    if endpoint.kind != "http":
        raise HTTPException(409, "This monitor receives observations through its assigned job or agent")
    try:
        return await monitor_service.perform_check(db, endpoint)
    except monitor_service.CheckInProgress as exc:
        raise HTTPException(409, "A check is already running") from exc
    except monitor_service.EndpointRemovedDuringCheck as exc:
        raise HTTPException(404, "Endpoint was removed during the check") from exc


@router.get(
    "/{endpoint_id}/checks",
    response_model=List[CheckResultResponse],
    summary="List recent health check results",
)
def get_endpoint_checks(
    endpoint_id: int,
    owner_id: WorkspaceOwner,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return monitor_service.list_check_results(db, endpoint_id, limit)


@router.get(
    "/{endpoint_id}/status",
    response_model=EndpointStatusResponse,
    summary="Get the latest health status for an endpoint",
)
def get_endpoint_status(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return monitor_service.get_status_summary(db, endpoint_id)


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a monitored endpoint",
)
def delete_endpoint(
    endpoint_id: int, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    endpoint_service.delete_endpoint(db, endpoint, actor.username)


@router.get("/{endpoint_id}/incidents", response_model=list[EndpointIncidentResponse])
def get_endpoint_incidents(
    endpoint_id: int,
    owner_id: WorkspaceOwner,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return incident_service.list_incidents(db, endpoint_id, limit)


@router.get("/{endpoint_id}/incident-records", response_model=list[IncidentRecord])
def get_incident_records(
    endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return list(db.scalars(select(Incident).where(Incident.endpoint_id == endpoint_id)
                           .order_by(Incident.opened_at.desc(), Incident.id.desc()).limit(100)))


def _incident_or_404(db: Session, endpoint_id: int, incident_id: int) -> Incident:
    incident = db.scalar(select(Incident).where(
        Incident.endpoint_id == endpoint_id, Incident.id == incident_id,
    ))
    if incident is None:
        raise HTTPException(404, "Incident not found")
    return incident


@router.get("/{endpoint_id}/incident-records/{incident_id}", response_model=IncidentRecord)
def get_incident_record(
    endpoint_id: int, incident_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return _incident_or_404(db, endpoint_id, incident_id)


@router.post("/{endpoint_id}/incident-records/{incident_id}/acknowledge", response_model=IncidentRecord)
def acknowledge_incident(
    endpoint_id: int, incident_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    db.execute(update(Incident).where(
        Incident.endpoint_id == endpoint_id, Incident.id == incident_id,
        Incident.acknowledged_at.is_(None), Incident.recovered_at.is_(None),
    ).values(acknowledged_at=datetime.now(timezone.utc)))
    incident = _incident_or_404(db, endpoint_id, incident_id)
    cancel_escalations(db, incident.id)
    db.commit()
    return incident


@router.post("/{endpoint_id}/incident-records/{incident_id}/notes", response_model=IncidentRecord)
def add_incident_note(
    endpoint_id: int, incident_id: int, payload: IncidentNote, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    # Acquire a write lock before reading the JSON notes on SQLite and PostgreSQL.
    db.execute(update(Incident).where(
        Incident.endpoint_id == endpoint_id, Incident.id == incident_id,
    ).values(notes=Incident.notes))
    incident = _incident_or_404(db, endpoint_id, incident_id)
    if len(incident.notes) >= 100:
        raise HTTPException(409, "This incident has reached the note limit")
    incident.notes = [*incident.notes, {
        "text": payload.text, "author": actor.username, "at": datetime.now(timezone.utc).isoformat(),
    }]
    db.commit()
    return incident


@router.get("/{endpoint_id}/maintenance", response_model=list[MaintenanceRecord])
def list_maintenance(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return list(db.scalars(select(MaintenanceWindow).where(
        MaintenanceWindow.endpoint_id == endpoint_id,
    ).order_by(MaintenanceWindow.starts_at.desc()).limit(100)))


@router.post("/{endpoint_id}/maintenance", status_code=201, response_model=MaintenanceRecord)
def add_maintenance(
    endpoint_id: int, payload: MaintenanceCreate, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    window = MaintenanceWindow(endpoint_id=endpoint_id, **payload.model_dump())
    db.add(window)
    db.flush()
    audit(db, actor.username, owner_id, "maintenance.created", window.id)
    db.commit()
    return window


@router.get("/{endpoint_id}/deliveries")
def list_deliveries(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    deliveries = db.scalars(select(AlertDelivery).where(
        AlertDelivery.endpoint_id == endpoint_id,
    ).order_by(AlertDelivery.id.desc()).limit(100))
    return [{
        "id": row.id, "event": row.payload["event"], "channel": row.channel, "state": row.state,
        "attempts": row.attempts, "created_at": row.created_at, "next_attempt_at": row.next_attempt_at,
        "last_error": row.last_error, "last_attempt_at": row.last_attempt_at,
    } for row in deliveries]


@router.get("/{endpoint_id}/metrics")
def get_metrics(
    endpoint_id: int, owner_id: WorkspaceOwner,
    hours: Literal["1", "24", "168", "720"] = "24", db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return monitor_service.range_metrics(db, endpoint_id, int(hours))


@router.get("/{endpoint_id}/deployments", response_model=list[DeploymentRecord])
def list_deployments(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return list(db.scalars(select(DeploymentMarker).where(
        DeploymentMarker.endpoint_id == endpoint_id,
    ).order_by(DeploymentMarker.deployed_at.desc(), DeploymentMarker.id.desc()).limit(100)))


@router.post("/{endpoint_id}/deployments", status_code=201, response_model=DeploymentRecord)
def record_deployment(
    endpoint_id: int, owner_id: WorkspaceOwner, payload: DeploymentCreate, db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    deployment = DeploymentMarker(
        endpoint_id=endpoint_id, label=payload.label, revision=payload.revision,
        deployed_at=payload.deployed_at or datetime.now(timezone.utc),
    )
    db.add(deployment)
    db.commit()
    return deployment


@router.get("/{endpoint_id}/incident-records/{incident_id}/deployments", response_model=list[DeploymentRecord])
def incident_deployments(
    endpoint_id: int, incident_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    incident = _incident_or_404(db, endpoint_id, incident_id)
    return list(db.scalars(select(DeploymentMarker).where(
        DeploymentMarker.endpoint_id == endpoint_id,
        DeploymentMarker.deployed_at >= incident.first_failed_at - timedelta(hours=1),
        DeploymentMarker.deployed_at <= (incident.recovered_at or datetime.now(timezone.utc)),
    ).order_by(DeploymentMarker.deployed_at, DeploymentMarker.id).limit(100)))


@router.get("/{endpoint_id}/reliability")
def monitor_reliability(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    return monitor_service.reliability(db, endpoint)


@router.get("/{endpoint_id}/incident-records/{incident_id}/report")
def get_incident_report(endpoint_id: int, incident_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    incident = _incident_or_404(db, endpoint_id, incident_id)
    return {
        "affected_services": [endpoint.name],
        "detected_at": incident.opened_at,
        **IncidentRecord.model_validate(incident).model_dump(),
    }


@router.patch("/{endpoint_id}/incident-records/{incident_id}/report", response_model=IncidentRecord)
def save_incident_report(
    endpoint_id: int, incident_id: int, payload: IncidentReportUpdate,
    owner_id: WorkspaceOwner, actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    incident = _incident_or_404(db, endpoint_id, incident_id)
    if "public_update" in payload.model_fields_set:
        audit(db, actor.username, owner_id, "incident.public_update", incident_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(incident, field, value)
    db.commit()
    return incident


@router.get("/{endpoint_id}/checks.csv")
def export_checks(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    model = CheckResult
    columns = (model.id, model.checked_at, model.is_success, model.status_code, model.response_time_ms,
               model.is_slow, model.failure_kind, model.failed_step, model.error)
    return _export_csv(db, model, columns, endpoint_id, "checks")


@router.get("/{endpoint_id}/incidents.csv")
def export_incidents(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    model = Incident
    columns = (model.id, model.first_failed_at, model.opened_at, model.acknowledged_at, model.recovered_at,
               model.first_error, model.root_cause, model.follow_up)
    return _export_csv(db, model, columns, endpoint_id, "incidents")


def _export_csv(db, model, columns, endpoint_id, name):
    scope = (model.endpoint_id == endpoint_id,)
    final_id = db.scalar(select(func.max(model.id)).where(*scope)) or 0
    return StreamingResponse(
        stream_rows(db.get_bind(), columns, model, scope, final_id),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="monitor-{endpoint_id}-{name}.csv"',
                 "Cache-Control": "private, no-store"},
    )


@router.get("/{endpoint_id}/incident-records/{incident_id}/related")
def related_incidents(endpoint_id: int, incident_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    incident = _incident_or_404(db, endpoint_id, incident_id)
    if endpoint.service_group is None:
        return []
    window = timedelta(minutes=30)
    return [{
        "endpoint_id": candidate.endpoint_id, "incident_id": candidate.id,
        "endpoint_name": name, "opened_at": candidate.opened_at, "service_group": endpoint.service_group,
    } for candidate, name in db.execute(select(Incident, Endpoint.name).join(
        Endpoint, Endpoint.id == Incident.endpoint_id,
    ).where(
        Endpoint.owner_id == owner_id, Endpoint.service_group == endpoint.service_group, Endpoint.id != endpoint.id,
        Incident.opened_at.between(incident.opened_at - window, incident.opened_at + window),
    ).order_by(Incident.opened_at, Incident.id).limit(25))]


@router.post("/{endpoint_id}/maintenance/{window_id}/cancel", response_model=MaintenanceRecord)
def cancel_maintenance(
    endpoint_id: int, window_id: int, owner_id: WorkspaceOwner,
    actor: User = Depends(authorize_endpoint), db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    now = datetime.now(timezone.utc)
    predicate = (MaintenanceWindow.endpoint_id == endpoint_id, MaintenanceWindow.id == window_id)
    changed = db.execute(update(MaintenanceWindow).where(
        *predicate, MaintenanceWindow.canceled_at.is_(None), MaintenanceWindow.ends_at > now,
    ).values(canceled_at=now)).rowcount
    if changed:
        audit(db, actor.username, owner_id, "maintenance.canceled", window_id)
    window = db.scalar(select(MaintenanceWindow).where(*predicate))
    if window is None:
        raise HTTPException(404, "Maintenance window not found")
    db.commit()
    return window
