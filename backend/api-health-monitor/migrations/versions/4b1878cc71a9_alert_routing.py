"""Add monitor classification, scoped routes and audit events."""
import sqlalchemy as sa
from alembic import op

revision = "4b1878cc71a9"
down_revision = "f02cd9147d63"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("endpoints", sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("endpoints", sa.Column("service_group", sa.String(80)))
    op.add_column("endpoints", sa.Column("severity", sa.String(12), nullable=False, server_default="warning"))
    op.create_table(
        "alert_integrations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer()),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("service_group", sa.String(80)),
        sa.Column("tag", sa.String(40)),
        sa.Column("endpoint_id", sa.Integer()),
        sa.Column("severity", sa.String(12)),
        sa.Column("escalation_seconds", sa.Integer()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_alert_integrations_owner_id", "alert_integrations", ["owner_id"])
    op.add_column("alert_deliveries", sa.Column("integration_id", sa.Integer()))
    op.add_column("alert_deliveries", sa.Column("incident_id", sa.Integer()))
    op.add_column("alert_deliveries", sa.Column("dedup_key", sa.String(120)))
    op.create_index("ix_alert_deliveries_dedup_key", "alert_deliveries", ["dedup_key"], unique=True)
    op.add_column("alert_deliveries", sa.Column("last_error", sa.String(60)))
    op.add_column("alert_deliveries", sa.Column("last_attempt_at", sa.DateTime(timezone=True)))
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer()),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("subject_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_scope_time", "audit_events", ["owner_id", "created_at"])


def downgrade():
    raise RuntimeError("Downgrading would discard routing and audit data")
