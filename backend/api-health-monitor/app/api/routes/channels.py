"""Manage email and webhook alert channels."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import AdminUser
from app.api.schemas import (
    ChannelCreate,
    ChannelResponse,
    ChannelTestResponse,
    ChannelUpdate,
)
from app.db.database import get_db
from app.db.models import NotificationChannel
from app.services import notification_service

router = APIRouter(prefix="/channels", tags=["Alerts"])


def _get_channel_or_404(db: Session, channel_id: int) -> NotificationChannel:
    channel = db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert channel with ID {channel_id} not found",
        )
    return channel


@router.get(
    "",
    response_model=list[ChannelResponse],
    summary="List alert channels",
)
def list_channels(_: AdminUser, db: Session = Depends(get_db)):
    return notification_service.list_channels(db)


@router.post(
    "",
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an email or webhook alert channel",
)
def create_channel(
    payload: ChannelCreate,
    _: AdminUser,
    db: Session = Depends(get_db),
):
    try:
        target = notification_service.validate_channel_target(
            payload.kind, payload.target
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    channel = NotificationChannel(
        name=payload.name.strip(),
        kind=payload.kind,
        target=target,
        is_enabled=payload.is_enabled,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


@router.patch(
    "/{channel_id}",
    response_model=ChannelResponse,
    summary="Update or enable/disable an alert channel",
)
def update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    _: AdminUser,
    db: Session = Depends(get_db),
):
    channel = _get_channel_or_404(db, channel_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)

    kind = changes.get("kind", channel.kind)
    target = changes.get("target", channel.target)
    if "kind" in changes or "target" in changes:
        try:
            changes["target"] = notification_service.validate_channel_target(
                kind, target
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc

    for field, value in changes.items():
        setattr(channel, field, value)

    db.commit()
    db.refresh(channel)
    return channel


@router.delete(
    "/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an alert channel",
)
def delete_channel(
    channel_id: int, _: AdminUser, db: Session = Depends(get_db)
):
    channel = _get_channel_or_404(db, channel_id)
    db.delete(channel)
    db.commit()
    return None


@router.post(
    "/{channel_id}/test",
    response_model=ChannelTestResponse,
    summary="Send a test alert through a channel",
)
async def test_channel(
    channel_id: int, _: AdminUser, db: Session = Depends(get_db)
):
    channel = _get_channel_or_404(db, channel_id)
    sent, error = await notification_service.send_test_alert(db, channel)
    return ChannelTestResponse(sent=sent, error=error)
