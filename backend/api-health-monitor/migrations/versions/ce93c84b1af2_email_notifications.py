"""Add verified recipients, monitor selections, and delivery attempts."""
import sqlalchemy as sa
from alembic import op

revision = "ce93c84b1af2"
down_revision = "a94c9e7034bd"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("public_endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id", ondelete="CASCADE")),
        sa.Column("address", sa.String(254), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("confirmation_hash", sa.String(64), unique=True),
        sa.Column("confirmation_expires_at", sa.DateTime(timezone=True)),
        sa.Column("unsubscribe_hash", sa.String(64), unique=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("weekly_digest", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.UniqueConstraint("scope", "address", name="uq_email_recipients_scope_address"),
    )
    op.create_index("ix_email_recipients_user_id", "email_recipients", ["user_id"])
    op.create_index("ix_email_recipients_public_endpoint_id", "email_recipients", ["public_endpoint_id"])
    op.create_table(
        "email_monitors",
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("email_recipients.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("endpoint_id", sa.Integer(), sa.ForeignKey("endpoints.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_email_monitors_endpoint_id", "email_monitors", ["endpoint_id"])
    with op.batch_alter_table("alert_deliveries") as batch:
        batch.alter_column("endpoint_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("recipient_id", sa.Integer()))
        batch.add_column(sa.Column("accepted_at", sa.DateTime(timezone=True)))
        batch.create_foreign_key("fk_alert_deliveries_recipient", "email_recipients", ["recipient_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_alert_deliveries_recipient_id", ["recipient_id"])
    op.create_table(
        "alert_attempts",
        sa.Column("delivery_id", sa.Integer(), sa.ForeignKey("alert_deliveries.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("number", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error", sa.String(60)),
    )


def downgrade():
    raise RuntimeError("Downgrading would discard email preferences and delivery history")
