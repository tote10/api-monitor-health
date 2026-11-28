from datetime import datetime, timedelta, timezone
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import SAFE_METHODS, check_request_origin, require_user
from app.db.database import get_db
from app.db.models import AlertDelivery, EmailMonitor, EmailRecipient, Endpoint, User
from app.services.email_notifications import (
    account_owner, email_ready, published_page, queue_confirmation, queue_email, token_hash, unsubscribe_token,
)
from app.services.email_notifications import normalize_address
from app.web_pages import page_response

def check_write_origin(request: Request):
    if request.method not in SAFE_METHODS:
        check_request_origin(request)


router = APIRouter(tags=["Email notifications"], dependencies=[Depends(check_write_origin)])
private = APIRouter(prefix="/operations/email", dependencies=[Depends(require_user)])


class AddressInput(BaseModel):
    address: str = Field(max_length=254)

    @field_validator("address")
    @classmethod
    def validate_address(cls, value):
        return normalize_address(value)


class Preferences(BaseModel):
    monitor_ids: list[int] = Field(max_length=1000)
    weekly_digest: bool = False


class EmailToken(BaseModel):
    token: str = Field(min_length=30, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")


def require_email_config():
    if not email_ready():
        raise HTTPException(503, "Email notifications require SMTP and a public base URL")


def recipient_for_update(db: Session, user_id: int, recipient_id: int) -> EmailRecipient:
    recipient = db.scalar(select(EmailRecipient).where(
        EmailRecipient.id == recipient_id, EmailRecipient.user_id == user_id,
    ).with_for_update())
    if not recipient:
        raise HTTPException(404, "Recipient not found")
    return recipient


def recipient_record(db: Session, recipient: EmailRecipient) -> dict:
    monitor_ids = db.scalars(select(EmailMonitor.endpoint_id).where(
        EmailMonitor.recipient_id == recipient.id,
    ).order_by(EmailMonitor.endpoint_id)).all()
    last_test = db.scalar(select(AlertDelivery).where(
        AlertDelivery.recipient_id == recipient.id,
        AlertDelivery.payload["event"].as_string() == "email.test",
    ).order_by(AlertDelivery.id.desc()).limit(1))
    return {
        "id": recipient.id, "address": recipient.address, "verified": bool(recipient.verified_at),
        "monitor_ids": monitor_ids, "weekly_digest": recipient.weekly_digest,
        "last_test": {
            "state": last_test.state, "attempts": last_test.attempts,
            "accepted_at": last_test.accepted_at, "last_error": last_test.last_error,
        } if last_test else None,
    }


@private.get("")
def get_email_settings(response: Response, user: User = Depends(require_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "private, no-store"
    recipients = db.scalars(select(EmailRecipient).where(
        EmailRecipient.user_id == user.id,
    ).order_by(EmailRecipient.id))
    return {
        "configured": email_ready(),
        "recipients": [recipient_record(db, recipient) for recipient in recipients],
        "monitors": [{"id": row.id, "name": row.name} for row in db.execute(select(
            Endpoint.id, Endpoint.name,
        ).where(Endpoint.owner_id == account_owner(user)).order_by(Endpoint.name, Endpoint.id))],
    }


@private.post("/recipients", status_code=202)
def add_recipient(payload: AddressInput, user: User = Depends(require_user), db: Session = Depends(get_db)):
    require_email_config()
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    recipient = db.scalar(select(EmailRecipient).where(
        EmailRecipient.user_id == user.id, EmailRecipient.address == payload.address,
    ).with_for_update())
    if not recipient:
        count = db.scalar(select(func.count()).select_from(EmailRecipient).where(EmailRecipient.user_id == user.id))
        if count >= 10:
            raise HTTPException(409, "Remove a recipient before adding another")
        recipient = EmailRecipient(scope=f"user:{user.id}", user_id=user.id, address=payload.address)
        db.add(recipient)
    try:
        db.flush()
        queued = queue_confirmation(db, recipient, datetime.now(timezone.utc))
        db.commit()
    except IntegrityError as exc:
        raise HTTPException(409, "Recipient changed concurrently. Refresh and try again.") from exc
    return {"queued": queued}


@private.put("/recipients/{recipient_id}")
def save_preferences(recipient_id: int, payload: Preferences,
                     user: User = Depends(require_user), db: Session = Depends(get_db)):
    recipient = recipient_for_update(db, user.id, recipient_id)
    requested = set(payload.monitor_ids)
    owned = set(db.scalars(select(Endpoint.id).where(
        Endpoint.id.in_(requested), Endpoint.owner_id == account_owner(user),
    )))
    if requested != owned:
        raise HTTPException(404, "One or more monitors were not found")
    for selection in list(recipient.selections):
        if selection.endpoint_id in requested:
            requested.remove(selection.endpoint_id)
        else:
            recipient.selections.remove(selection)
    recipient.selections.extend(EmailMonitor(endpoint_id=endpoint_id) for endpoint_id in sorted(requested))
    recipient.weekly_digest = payload.weekly_digest
    db.commit()
    return recipient_record(db, recipient)


@private.post("/recipients/{recipient_id}/test", status_code=202)
def send_test(recipient_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    require_email_config()
    recipient = recipient_for_update(db, user.id, recipient_id)
    if not recipient.verified_at:
        raise HTTPException(409, "Verify the email address first")
    now = datetime.now(timezone.utc)
    recent = db.scalar(select(AlertDelivery.id).where(
        AlertDelivery.recipient_id == recipient.id,
        AlertDelivery.payload["event"].as_string() == "email.test",
        AlertDelivery.created_at >= now - timedelta(minutes=1),
    ).limit(1))
    if recent:
        raise HTTPException(429, "Wait one minute before sending another test")
    queue_email(db, recipient, {"event": "email.test"}, now)
    db.commit()
    return {"queued": True}


@private.delete("/recipients/{recipient_id}", status_code=204)
def remove_recipient(recipient_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    db.delete(recipient_for_update(db, user.id, recipient_id))
    db.commit()
    return Response(status_code=204)


@router.get("/email/{action}", include_in_schema=False)
def email_action_page(action: str):
    if action not in {"confirm", "unsubscribe"}:
        raise HTTPException(404)
    return page_response("email_action.html", headers={"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex"})


@router.post("/email/confirm")
def confirm_email(payload: EmailToken, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    recipient = db.scalar(select(EmailRecipient).where(
        EmailRecipient.confirmation_hash == token_hash(payload.token),
        EmailRecipient.confirmation_expires_at > now, EmailRecipient.disabled_at.is_(None),
    ).with_for_update())
    if not recipient:
        raise HTTPException(404, "Confirmation link is invalid or expired")
    if recipient.public_endpoint_id is not None:
        endpoint = db.get(Endpoint, recipient.public_endpoint_id)
        if not endpoint or not published_page(db, endpoint):
            raise HTTPException(404, "This service is no longer published")
    recipient.verified_at = now
    recipient.confirmation_hash = None
    recipient.confirmation_expires_at = None
    db.commit()
    return {"message": "Email address confirmed."}


@router.post("/email/unsubscribe")
def unsubscribe(payload: EmailToken, db: Session = Depends(get_db)):
    identifier, _, _ = payload.token.partition(".")
    recipient = db.get(EmailRecipient, int(identifier), with_for_update=True) if identifier.isascii() and identifier.isdigit() and len(identifier) <= 10 else None
    if not recipient or not recipient.unsubscribe_hash or not hmac.compare_digest(unsubscribe_token(recipient), payload.token):
        raise HTTPException(404, "Unsubscribe link is invalid")
    recipient.disabled_at = datetime.now(timezone.utc)
    recipient.confirmation_hash = None
    recipient.confirmation_expires_at = None
    db.commit()
    return {"message": "You have unsubscribed from this service."}


router.include_router(private)
