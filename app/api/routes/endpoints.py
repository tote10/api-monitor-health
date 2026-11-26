from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    CheckResultResponse,
    EndpointCreate,
    EndpointIncidentResponse,
    EndpointResponse,
    EndpointStatusResponse,
    EndpointUpdate,
)
from app.core.auth import authorize_endpoint
from app.db.database import get_db
from app.db.models import Endpoint, User
from app.services import endpoint_service, incident_service, monitor_service
from app.services.endpoint_service import EndpointAlreadyExists

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
def create_endpoint(payload: EndpointCreate, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    try:
        return endpoint_service.register_endpoint(db, payload, owner_id)
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
    db: Session = Depends(get_db),
):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    try:
        return endpoint_service.update_endpoint(db, endpoint, payload)
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
    try:
        return await monitor_service.perform_check(db, endpoint)
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
def delete_endpoint(endpoint_id: int, owner_id: WorkspaceOwner, db: Session = Depends(get_db)):
    endpoint = _get_endpoint_or_404(db, endpoint_id, owner_id)
    endpoint_service.delete_endpoint(db, endpoint)


@router.get("/{endpoint_id}/incidents", response_model=list[EndpointIncidentResponse])
def get_endpoint_incidents(
    endpoint_id: int,
    owner_id: WorkspaceOwner,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id, owner_id)
    return incident_service.list_incidents(db, endpoint_id, limit)
