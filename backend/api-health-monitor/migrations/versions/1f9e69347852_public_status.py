"""Add opt-in status page settings and explicitly public presentation fields."""
import sqlalchemy as sa
from alembic import op

revision = "1f9e69347852"
down_revision = "4b1878cc71a9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("endpoints", sa.Column("public_label", sa.String(100)))
    op.add_column("incidents", sa.Column("public_update", sa.String(1000)))
    op.create_table(
        "status_pages",
        sa.Column("scope_key", sa.String(64), primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False, unique=True),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
    )


def downgrade():
    raise RuntimeError("Downgrading would discard public status page settings")
