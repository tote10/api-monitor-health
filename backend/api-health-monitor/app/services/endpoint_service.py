from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import EndpointCreate, EndpointUpdate
from app.db.models import Endpoint
from app.services.url_guard import validate_target_url


class EndpointAlreadyExists(Exception):
    """Raised when an endpoint URL is already registered."""


def _normalize_url(url: HttpUrl) -> str:
    """Store URLs without a trailing slash so duplicates are detected."""
    return str(url).rstrip("/")


def register_endpoint(db: Session, data: EndpointCreate) -> Endpoint:
    validate_target_url(_normalize_url(data.url))

    endpoint = Endpoint(
        name=data.name,
        url=_normalize_url(data.url),
        interval_seconds=data.interval_seconds,
        timeout_seconds=data.timeout_seconds,
        expected_status_code=data.expected_status_code,
        is_active=data.is_active,
        alerts_enabled=data.alerts_enabled,
    )

    db.add(endpoint)

    try:
        db.commit()
        db.refresh(endpoint)
    except IntegrityError as exc:
        db.rollback()
        raise EndpointAlreadyExists(
            "An endpoint with this URL already exists."
        ) from exc

    return endpoint


def list_endpoints(db: Session) -> list[Endpoint]:
    statement = select(Endpoint).order_by(Endpoint.id)
    return list(db.scalars(statement).all())


def get_endpoint_by_id(
    db: Session, endpoint_id: int
) -> Endpoint | None:
    return db.get(Endpoint, endpoint_id)


def update_endpoint(
    db: Session, endpoint_id: int, data: EndpointUpdate
) -> Endpoint | None:
    endpoint = db.get(Endpoint, endpoint_id)

    if endpoint is None:
        return None

    changes = data.model_dump(exclude_unset=True, exclude_none=True)

    if "url" in changes:
        changes["url"] = _normalize_url(changes["url"])
        validate_target_url(changes["url"])

    for field, value in changes.items():
        setattr(endpoint, field, value)

    try:
        db.commit()
        db.refresh(endpoint)
    except IntegrityError as exc:
        db.rollback()
        raise EndpointAlreadyExists(
            "An endpoint with this URL already exists."
        ) from exc

    return endpoint


def delete_endpoint(db: Session, endpoint_id: int) -> bool:
    endpoint = db.get(Endpoint, endpoint_id)

    if endpoint is None:
        return False

    db.delete(endpoint)
    db.commit()
    return True