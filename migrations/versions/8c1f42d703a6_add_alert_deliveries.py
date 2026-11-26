"""Add a persistent notification delivery queue."""

from alembic import op
import sqlalchemy as sa

revision = "8c1f42d703a6"
down_revision = "c48fa65b91d2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "alert_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_alert_deliveries_pending", "alert_deliveries", ["state", "next_attempt_at"])
    op.create_index(
        "ix_alert_deliveries_endpoint_channel", "alert_deliveries", ["endpoint_id", "channel", "id"]
    )


def downgrade():
    op.drop_index("ix_alert_deliveries_endpoint_channel", table_name="alert_deliveries")
    op.drop_index("ix_alert_deliveries_pending", table_name="alert_deliveries")
    op.drop_table("alert_deliveries")
