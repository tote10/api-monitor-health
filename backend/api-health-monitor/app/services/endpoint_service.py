from pydantic import HttpUrl
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import EndpointCreate, EndpointUpdate
from app.db.models import AlertIntegration, Endpoint
from app.services.audit import audit
from app.services.request_config import monitor_cipher


class EndpointAlreadyExists(Exception):
    """Raised when an endpoint URL is already registered."""


def _normalize_url(url: HttpUrl) -> str:
    """Store URLs without a trailing slash so duplicates are detected."""
    return str(url).rstrip("/")


def register_endpoint(db: Session, data: EndpointCreate, owner_id: int | None, actor: str | None = None) -> Endpoint:
    fields = data.model_dump(exclude={"request_options", "url"})
    endpoint = Endpoint(owner_id=owner_id, url=_normalize_url(data.url), **fields)
    if data.request_options is not None:
        endpoint.request_config = monitor_cipher().encrypt(data.request_options.model_dump_json().encode()).decode()

    db.add(endpoint)

    try:
        if actor:
            db.flush()
            audit(db, actor, owner_id, "monitor.created", endpoint.id)
        db.commit()
        db.refresh(endpoint)
    except IntegrityError as exc:
        db.rollback()
        raise EndpointAlreadyExists(
            "An endpoint with this URL already exists."
        ) from exc

    return endpoint


def list_endpoints(db: Session, owner_id: int | None) -> list[Endpoint]:
    statement = select(Endpoint).where(Endpoint.owner_id == owner_id).order_by(Endpoint.id)
    return list(db.scalars(statement).all())


def get_endpoint_by_id(
    db: Session, endpoint_id: int, owner_id: int | None
) -> Endpoint | None:
    return db.scalar(select(Endpoint).where(Endpoint.id == endpoint_id, Endpoint.owner_id == owner_id))


def update_endpoint(
    db: Session, endpoint: Endpoint, data: EndpointUpdate, actor: str | None = None
) -> Endpoint:

    changes = data.model_dump(exclude_unset=True, exclude_none=True, exclude={"request_options"})
    if "request_options" in data.model_fields_set:
        endpoint.request_config = (
            monitor_cipher().encrypt(data.request_options.model_dump_json().encode()).decode()
            if data.request_options is not None else None
        )
    for field in ("expected_status_codes", "latency_budget_ms", "slo_target_percentage", "service_group", "public_label", "retention_days"):
        if field in data.model_fields_set:
            changes[field] = getattr(data, field)
    if "expected_status_code" in changes and "expected_status_codes" not in changes:
        changes["expected_status_codes"] = None

    if "url" in changes:
        changes["url"] = _normalize_url(changes["url"])

    for field, value in changes.items():
        setattr(endpoint, field, value)

    try:
        if actor:
            audit(db, actor, endpoint.owner_id, "monitor.updated", endpoint.id)
            if "public_label" in changes:
                audit(db, actor, endpoint.owner_id, "monitor.public_status", endpoint.id)
        db.commit()
        db.refresh(endpoint)
    except IntegrityError as exc:
        db.rollback()
        raise EndpointAlreadyExists(
            "An endpoint with this URL already exists."
        ) from exc

    return endpoint


def delete_endpoint(db: Session, endpoint: Endpoint, actor: str | None = None) -> None:
    if actor:
        audit(db, actor, endpoint.owner_id, "monitor.deleted", endpoint.id)
    db.execute(update(AlertIntegration).where(
        AlertIntegration.endpoint_id == endpoint.id,
        AlertIntegration.owner_id == endpoint.owner_id,
    ).values(enabled=False))
    db.delete(endpoint)
    db.commit()
