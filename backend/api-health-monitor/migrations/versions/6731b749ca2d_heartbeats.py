"""Add heartbeat monitor settings and revocable credentials."""
import sqlalchemy as sa
from alembic import op

revision = "6731b749ca2d"
down_revision = "5a11d28dc8f1"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("kind", sa.String(12), nullable=False, server_default="http"),
        sa.Column("heartbeat_grace_seconds", sa.Integer()),
        sa.Column("heartbeat_token_hash", sa.String(64)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("endpoints", column)


def downgrade():
    raise RuntimeError("Downgrading would discard heartbeat configuration")
