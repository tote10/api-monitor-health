"""Store observed TLS expiry and monitor-specific history retention."""
import sqlalchemy as sa
from alembic import op

revision = "9cb514e8e037"
down_revision = "1f9e69347852"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("endpoints", sa.Column("retention_days", sa.Integer()))
    op.add_column("check_results", sa.Column("certificate_expires_at", sa.DateTime(timezone=True)))


def downgrade():
    raise RuntimeError("Downgrading would discard TLS observations and retention settings")
