"""Scope endpoints to the shared workspace or an invited member."""

import sqlalchemy as sa
from alembic import op

revision = "71d2a68f504e"
down_revision = "2c4f10ae7b93"
branch_labels = None
depends_on = None

NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade():
    connection = op.get_bind()
    url_constraint = next(
        constraint["name"]
        for constraint in sa.inspect(connection).get_unique_constraints("endpoints")
        if constraint["column_names"] == ["url"]
    )
    with op.batch_alter_table("endpoints", naming_convention=NAMING) as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_endpoints_owner_id_users", "users", ["owner_id"], ["id"], ondelete="RESTRICT",
        )
        batch.drop_constraint(url_constraint or "uq_endpoints_url", type_="unique")

    op.create_index("uq_endpoints_owner_url", "endpoints", ["owner_id", "url"], unique=True)
    shared = sa.text("owner_id IS NULL")
    op.create_index(
        "uq_endpoints_shared_url", "endpoints", ["url"], unique=True,
        sqlite_where=shared, postgresql_where=shared,
    )
    op.execute(sa.text("UPDATE account_invites SET role = 'member'"))


def downgrade():
    connection = op.get_bind()
    for statement in (
        "SELECT 1 FROM endpoints WHERE owner_id IS NOT NULL LIMIT 1",
        "SELECT 1 FROM users WHERE role = 'member' LIMIT 1",
        "SELECT 1 FROM account_invites WHERE role = 'member' LIMIT 1",
    ):
        if connection.execute(sa.text(statement)).first() is not None:
            raise RuntimeError("Cannot downgrade while personal workspaces or their invitations exist")

    op.drop_index("uq_endpoints_shared_url", table_name="endpoints")
    op.drop_index("uq_endpoints_owner_url", table_name="endpoints")
    with op.batch_alter_table("endpoints", naming_convention=NAMING) as batch:
        batch.drop_constraint("fk_endpoints_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")
        batch.create_unique_constraint("uq_endpoints_url", ["url"])
