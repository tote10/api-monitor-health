"""Add monitor reliability targets and editable incident reports."""
import sqlalchemy as sa
from alembic import op

revision = "f02cd9147d63"
down_revision = "89e172a03f6b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("endpoints", sa.Column("slo_target_percentage", sa.Float()))
    op.add_column("endpoints", sa.Column("slo_window_hours", sa.Integer(), nullable=False, server_default="720"))
    op.add_column("incidents", sa.Column("root_cause", sa.String(4000)))
    op.add_column("incidents", sa.Column("follow_up", sa.String(4000)))


def downgrade():
    raise RuntimeError("Downgrading would discard reliability settings and report text")
