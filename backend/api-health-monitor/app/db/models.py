from datetime import datetime, timezone

from sqlalchemy import (
    UniqueConstraint,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    func,
    text,
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
    __table_args__ = (
        Index(
            "uq_endpoints_shared_url", "url", unique=True,
            sqlite_where=text("owner_id IS NULL"), postgresql_where=text("owner_id IS NULL"),
        ),
        Index("uq_endpoints_owner_url", "owner_id", "url", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_endpoints_owner_id_users", ondelete="RESTRICT")
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
    kind: Mapped[str] = mapped_column(String(12), default="http", server_default="http")
    agent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    agent_target: Mapped[str | None] = mapped_column(String(80))
    heartbeat_grace_seconds: Mapped[int | None]
    heartbeat_token_hash: Mapped[str | None] = mapped_column(String(64))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    method: Mapped[str] = mapped_column(String(8), default="GET", server_default="GET")
    expected_status_codes: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    latency_budget_ms: Mapped[int | None]
    request_config: Mapped[str | None] = mapped_column(String)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    service_group: Mapped[str | None] = mapped_column(String(80))
    public_label: Mapped[str | None] = mapped_column(String(100))
    retention_days: Mapped[int | None]
    severity: Mapped[str] = mapped_column(String(12), default="warning", server_default="warning")
    slo_target_percentage: Mapped[float | None] = mapped_column(Float)
    slo_window_hours: Mapped[int] = mapped_column(default=720, server_default="720")
    failure_threshold: Mapped[int] = mapped_column(default=1, server_default="1")
    recovery_threshold: Mapped[int] = mapped_column(default=1, server_default="1")
    check_epoch: Mapped[int] = mapped_column(default=0, server_default="0")
    check_token: Mapped[str | None] = mapped_column(String(32))
    check_lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())

    deployments: Mapped[list["DeploymentMarker"]] = relationship(cascade="all, delete-orphan")
    incidents: Mapped[list["Incident"]] = relationship(cascade="all, delete-orphan")
    maintenance_windows: Mapped[list["MaintenanceWindow"]] = relationship(cascade="all, delete-orphan")
    check_results: Mapped[list["CheckResult"]] = relationship(
        back_populates="endpoint",
        cascade="all, delete-orphan",
    )
    email_recipients: Mapped[list["EmailRecipient"]] = relationship(cascade="all, delete-orphan")
    email_monitors: Mapped[list["EmailMonitor"]] = relationship(cascade="all, delete-orphan")
    alert_deliveries: Mapped[list["AlertDelivery"]] = relationship(
        cascade="all, delete-orphan",
    )

    @property
    def has_request_options(self) -> bool:
        return self.request_config is not None


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (
        Index(
            "ix_check_results_endpoint_checked_at",
            "endpoint_id",
            "checked_at",
        ),
        Index("ix_check_results_checked_at", "checked_at"),
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

    failure_kind: Mapped[str | None] = mapped_column(String(24))
    is_slow: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    failed_step: Mapped[int | None]
    certificate_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    endpoint: Mapped[Endpoint] = relationship(back_populates="check_results")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(16))
    email_recipients: Mapped[list["EmailRecipient"]] = relationship(cascade="all, delete-orphan")


class LoginSession(Base):
    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class EmailRecipient(Base):
    __tablename__ = "email_recipients"
    __table_args__ = (UniqueConstraint("scope", "address", name="uq_email_recipients_scope_address"),)

    selections: Mapped[list["EmailMonitor"]] = relationship(cascade="all, delete-orphan")
    deliveries: Mapped[list["AlertDelivery"]] = relationship(cascade="all, delete-orphan")

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    public_endpoint_id: Mapped[int | None] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(String(254))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    confirmation_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    unsubscribe_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    weekly_digest: Mapped[bool] = mapped_column(default=False, server_default=text("false"))


class EmailMonitor(Base):
    __tablename__ = "email_monitors"

    recipient_id: Mapped[int] = mapped_column(ForeignKey("email_recipients.id", ondelete="CASCADE"), primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"), primary_key=True, index=True)


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"
    __table_args__ = (
        Index("ix_alert_deliveries_pending", "state", "next_attempt_at"),
        Index("ix_alert_deliveries_endpoint_channel", "endpoint_id", "channel", "id"),
    )

    attempt_history: Mapped[list["AlertAttempt"]] = relationship(cascade="all, delete-orphan")

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int | None] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("email_recipients.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int | None] = mapped_column(Integer)
    incident_id: Mapped[int | None] = mapped_column(Integer)
    dedup_key: Mapped[str | None] = mapped_column(String(120), index=True, unique=True)
    last_error: Mapped[str | None] = mapped_column(String(60))
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivery_token: Mapped[str | None] = mapped_column(String(32))
    delivery_lease_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AlertAttempt(Base):
    __tablename__ = "alert_attempts"

    delivery_id: Mapped[int] = mapped_column(ForeignKey("alert_deliveries.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    outcome: Mapped[str] = mapped_column(String(16), default="in_progress")
    error: Mapped[str | None] = mapped_column(String(60))


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("uq_incidents_open_endpoint", "endpoint_id", unique=True,
              sqlite_where=text("recovered_at IS NULL"), postgresql_where=text("recovered_at IS NULL")),
        Index("ix_incidents_endpoint_opened", "endpoint_id", "opened_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    first_failed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    recovered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    first_error: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[list[dict]] = mapped_column(JSON, default=list)
    root_cause: Mapped[str | None] = mapped_column(String(4000))
    follow_up: Mapped[str | None] = mapped_column(String(4000))
    public_update: Mapped[str | None] = mapped_column(String(1000))


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    __table_args__ = (Index("ix_maintenance_endpoint_start", "endpoint_id", "starts_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    title: Mapped[str] = mapped_column(String(100))


class AccountInvite(Base):
    __tablename__ = "account_invites"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    email_recipients: Mapped[list["EmailRecipient"]] = relationship(cascade="all, delete-orphan")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class DeploymentMarker(Base):
    __tablename__ = "deployment_markers"
    __table_args__ = (Index("ix_deployment_endpoint_time", "endpoint_id", "deployed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("endpoints.id", ondelete="CASCADE"))
    deployed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    label: Mapped[str] = mapped_column(String(120))
    revision: Mapped[str | None] = mapped_column(String(100))


class SchedulerWorker(Base):
    __tablename__ = "scheduler_workers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_poll_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    state: Mapped[str] = mapped_column(String(16))
    last_saved: Mapped[int]


class AlertIntegration(Base):
    __tablename__ = "alert_integrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(12))
    destination: Mapped[str] = mapped_column(String)
    service_group: Mapped[str | None] = mapped_column(String(80))
    tag: Mapped[str | None] = mapped_column(String(40))
    endpoint_id: Mapped[int | None]
    severity: Mapped[str | None] = mapped_column(String(12))
    escalation_seconds: Mapped[int | None]
    enabled: Mapped[bool] = mapped_column(default=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_scope_time", "owner_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None]
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(60))
    subject_id: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())



class StatusPage(Base):
    __tablename__ = "status_pages"

    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool]



class PrivateAgent(Base):
    __tablename__ = "private_agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(80))
    token_hash: Mapped[str | None] = mapped_column(String(64))
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
