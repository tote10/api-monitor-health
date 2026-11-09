from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.schemas import EndpointCreate, EndpointResponse, EndpointUpdate
from app.db.database import get_db
from app.services import endpoint_service
from app.services.endpoint_service import EndpointAlreadyExists

router = APIRouter(prefix="/endpoints", tags=["Endpoints"])


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
    endpoint = endpoint_service.get_endpoint_by_id(db, endpoint_id)
    if not endpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint with ID {endpoint_id} not found",
        )
    return endpoint


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
