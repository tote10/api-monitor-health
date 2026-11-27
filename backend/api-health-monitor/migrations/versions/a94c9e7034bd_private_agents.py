"""Register per-workspace private monitoring agents."""
import sqlalchemy as sa
from alembic import op

revision = "a94c9e7034bd"
down_revision = "9cb514e8e037"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "private_agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer()),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_private_agents_owner_id", "private_agents", ["owner_id"])
    op.add_column("endpoints", sa.Column("agent_id", sa.Integer()))
    op.add_column("endpoints", sa.Column("agent_target", sa.String(80)))
    op.create_index("ix_endpoints_agent_id", "endpoints", ["agent_id"])


def downgrade():
    raise RuntimeError("Downgrading would discard private agent configuration")
