"""Add deployment markers and scheduler health."""
import sqlalchemy as sa
from alembic import op

revision = "89e172a03f6b"
down_revision = "6731b749ca2d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deployment_markers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("revision", sa.String(100)),
    )
    op.create_index("ix_deployment_endpoint_time", "deployment_markers", ["endpoint_id", "deployed_at"])
    op.create_table(
        "scheduler_workers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("last_saved", sa.Integer(), nullable=False),
    )
    op.create_index("ix_scheduler_workers_last_poll_at", "scheduler_workers", ["last_poll_at"])


def downgrade():
    raise RuntimeError("Downgrading would discard deployment and diagnostic history")
