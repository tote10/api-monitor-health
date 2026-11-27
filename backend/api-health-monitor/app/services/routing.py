from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import AlertDelivery, AlertIntegration, Endpoint, Incident


def routed_delivery(endpoint, incident, integration, event, payload, due_at):
    return AlertDelivery(
        endpoint_id=endpoint.id, incident_id=incident.id if incident else None,
        integration_id=integration.id, channel="integration",
        dedup_key=f"{incident.id}:{integration.id}:{event}" if incident else None,
        payload={**payload, "event": event}, next_attempt_at=due_at,
    )


def queue_integrations(db: Session, endpoint: Endpoint, incident: Incident | None, payload: dict, now):
    routes = db.scalars(select(AlertIntegration).where(
        AlertIntegration.owner_id == endpoint.owner_id, AlertIntegration.enabled.is_(True),
    ))
    event = payload["event"]
    for integration in routes:
        if integration.endpoint_id is not None and integration.endpoint_id != endpoint.id:
            continue
        if integration.service_group and integration.service_group != endpoint.service_group:
            continue
        if integration.tag and integration.tag not in endpoint.tags:
            continue
        if integration.severity and integration.severity != endpoint.severity:
            continue
        delivery = routed_delivery(endpoint, incident, integration, event, payload, now)
        if incident is None and event == "endpoint.certificate_expiring":
            delivery.dedup_key = f"tls:{endpoint.id}:{integration.id}:{payload['certificate_expires_at']}"
            if has_delivery(db, delivery.dedup_key):
                continue
        db.add(delivery)
        if event == "endpoint.down" and endpoint.severity == "critical" and integration.escalation_seconds:
            due = now + timedelta(seconds=integration.escalation_seconds)
            db.add(routed_delivery(endpoint, incident, integration, "endpoint.escalated", payload, due))


def has_delivery(db: Session, key: str) -> bool:
    return any(isinstance(row, AlertDelivery) and row.dedup_key == key for row in db.new) or bool(
        db.scalar(select(AlertDelivery.id).where(AlertDelivery.dedup_key == key))
    )


def cancel_escalations(db: Session, incident_id: int):
    db.execute(update(AlertDelivery).where(
        AlertDelivery.incident_id == incident_id,
        AlertDelivery.state == "pending",
        AlertDelivery.payload["event"].as_string() == "endpoint.escalated",
        AlertDelivery.delivery_token.is_(None),
    ).values(state="canceled"))
