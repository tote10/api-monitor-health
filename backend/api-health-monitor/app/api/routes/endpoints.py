from typing import List
from fastapi import APIRouter, HTTPException, status

from app.api.schemas import EndpointCreate, EndpointResponse, EndpointUpdate
from app.services import endpoint_service

router = APIRouter(prefix="/endpoints", tags=["Endpoints"])


@router.post(
    "",
    response_model=EndpointResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new monitored endpoint",
)
def create_endpoint(payload: EndpointCreate):
    return endpoint_service.register_endpoint(payload)


@router.get(
    "",
    response_model=List[EndpointResponse],
    summary="List all monitored endpoints",
)
def get_endpoints():
    return endpoint_service.list_endpoints()


@router.get(
    "/{endpoint_id}",
    response_model=EndpointResponse,
    summary="Get endpoint details by ID",
)
def get_endpoint(endpoint_id: int):
    endpoint = endpoint_service.get_endpoint_by_id(endpoint_id)
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
def update_endpoint(endpoint_id: int, payload: EndpointUpdate):
    endpoint = endpoint_service.update_endpoint(endpoint_id, payload)
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
def delete_endpoint(endpoint_id: int):
    success = endpoint_service.delete_endpoint(endpoint_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint with ID {endpoint_id} not found",
        )
    return None
