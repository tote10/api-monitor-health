"""add timeout and expected status code to endpoints

Revision ID: 8f2c4d7a9b31
Revises: 303235634f48
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f2c4d7a9b31'
down_revision: Union[str, Sequence[str], None] = '303235634f48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the endpoint check configuration columns."""
    op.add_column(
        'endpoints',
        sa.Column(
            'timeout_seconds',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('5'),
        ),
    )
    op.add_column(
        'endpoints',
        sa.Column(
            'expected_status_code',
            sa.Integer(),
            nullable=False,
            server_default=sa.text('200'),
        ),
    )


def downgrade() -> None:
    """Remove the endpoint check configuration columns."""
    op.drop_column('endpoints', 'expected_status_code')
    op.drop_column('endpoints', 'timeout_seconds')
