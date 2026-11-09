from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.schemas import (
    CheckResultResponse,
    EndpointCreate,
    EndpointResponse,
    EndpointStatusResponse,
    EndpointUpdate,
)
from app.db.database import get_db
from app.db.models import Endpoint
from app.services import endpoint_service, monitor_service
from app.services.endpoint_service import EndpointAlreadyExists

router = APIRouter(prefix="/endpoints", tags=["Endpoints"])


def _get_endpoint_or_404(db: Session, endpoint_id: int) -> Endpoint:
    endpoint = endpoint_service.get_endpoint_by_id(db, endpoint_id)
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
def create_endpoint(payload: EndpointCreate, db: Session = Depends(get_db)):
    try:
        return endpoint_service.register_endpoint(db, payload)
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
def get_endpoints(db: Session = Depends(get_db)):
    return endpoint_service.list_endpoints(db)


@router.get(
    "/{endpoint_id}",
    response_model=EndpointResponse,
    summary="Get endpoint details by ID",
)
def get_endpoint(endpoint_id: int, db: Session = Depends(get_db)):
    return _get_endpoint_or_404(db, endpoint_id)


@router.patch(
    "/{endpoint_id}",
    response_model=EndpointResponse,
    summary="Update endpoint configuration",
)
def update_endpoint(
    endpoint_id: int,
    payload: EndpointUpdate,
    db: Session = Depends(get_db),
):
    try:
        endpoint = endpoint_service.update_endpoint(db, endpoint_id, payload)
    except EndpointAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint with ID {endpoint_id} not found",
        )
    return endpoint


@router.post(
    "/{endpoint_id}/check",
    response_model=CheckResultResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a health check for an endpoint now",
)
async def run_endpoint_check(endpoint_id: int, db: Session = Depends(get_db)):
    endpoint = _get_endpoint_or_404(db, endpoint_id)
    return await monitor_service.perform_check(db, endpoint)


@router.get(
    "/{endpoint_id}/checks",
    response_model=List[CheckResultResponse],
    summary="List recent health check results",
)
def get_endpoint_checks(
    endpoint_id: int,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    _get_endpoint_or_404(db, endpoint_id)
    return monitor_service.list_check_results(db, endpoint_id, limit)


@router.get(
    "/{endpoint_id}/status",
    response_model=EndpointStatusResponse,
    summary="Get the latest health status for an endpoint",
)
def get_endpoint_status(endpoint_id: int, db: Session = Depends(get_db)):
    _get_endpoint_or_404(db, endpoint_id)
    return monitor_service.get_status_summary(db, endpoint_id)


@router.delete(
    "/{endpoint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a monitored endpoint",
)
def delete_endpoint(endpoint_id: int, db: Session = Depends(get_db)):
    success = endpoint_service.delete_endpoint(db, endpoint_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint with ID {endpoint_id} not found",
        )
    return None
