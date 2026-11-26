"""Add single-use invitations for browser registration."""

import sqlalchemy as sa
from alembic import op

revision = "2c4f10ae7b93"
down_revision = "8c1f42d703a6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_invites",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_invites_expires_at", "account_invites", ["expires_at"])


def downgrade():
    op.drop_index("ix_account_invites_expires_at", table_name="account_invites")
    op.drop_table("account_invites")
