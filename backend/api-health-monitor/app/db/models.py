from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db.database import Base


class UTCDateTime(TypeDecorator):
    """Store timestamps as UTC and always read them back timezone-aware."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Endpoint(Base):
    __tablename__ = "endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(
        String(2048), nullable=False, unique=True
    )

    interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5
    )
    expected_status_code: Mapped[int] = mapped_column(
        Integer, nullable=False, default=200
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        nullable=False,
    )
    check_results: Mapped[list["CheckResult"]] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
    )


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (
        Index(
            "ix_check_results_endpoint_checked_at",
            "endpoint_id",
            "checked_at",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(
        ForeignKey("endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    checked_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        server_default=func.now(),
        nullable=False,
    )
    is_success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    endpoint: Mapped[Endpoint] = relationship(back_populates="check_results")
