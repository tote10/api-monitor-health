"""Add check claims, request options, incident state and maintenance."""
import sqlalchemy as sa
from alembic import op

revision = "5a11d28dc8f1"
down_revision = "71d2a68f504e"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("method", sa.String(8), nullable=False, server_default="GET"),
        sa.Column("expected_status_codes", sa.JSON()),
        sa.Column("latency_budget_ms", sa.Integer()),
        sa.Column("request_config", sa.String()),
        sa.Column("failure_threshold", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("recovery_threshold", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("check_epoch", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("check_token", sa.String(32)),
        sa.Column("check_lease_until", sa.DateTime(timezone=True)),
    ):
        op.add_column("endpoints", column)
    for column in (
        sa.Column("failure_kind", sa.String(24)),
        sa.Column("is_slow", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failed_step", sa.Integer()),
    ):
        op.add_column("check_results", column)
    op.add_column("alert_deliveries", sa.Column("delivery_token", sa.String(32)))
    op.add_column("alert_deliveries", sa.Column("delivery_lease_until", sa.DateTime(timezone=True)))
    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("first_error", sa.String(500)),
        sa.Column("notes", sa.JSON(), nullable=False),
    )
    open_incident = sa.text("recovered_at IS NULL")
    op.create_index("uq_incidents_open_endpoint", "incidents", ["endpoint_id"], unique=True,
                    sqlite_where=open_incident, postgresql_where=open_incident)
    op.create_index("ix_incidents_endpoint_opened", "incidents", ["endpoint_id", "opened_at"])
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canceled_at", sa.DateTime(timezone=True)),
        sa.Column("title", sa.String(100), nullable=False),
    )
    op.create_index("ix_maintenance_endpoint_start", "maintenance_windows", ["endpoint_id", "starts_at"])


def downgrade():
    raise RuntimeError("Downgrading this revision would discard incident and monitoring settings")
