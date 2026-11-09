"""create check results table

Revision ID: b7e4f1a9c2d3
Revises: 8f2c4d7a9b31
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e4f1a9c2d3'
down_revision: Union[str, Sequence[str], None] = '8f2c4d7a9b31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the table that stores health check results."""
    op.create_table(
        'check_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('endpoint_id', sa.Integer(), nullable=False),
        sa.Column(
            'checked_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column('is_success', sa.Boolean(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('response_time_ms', sa.Integer(), nullable=True),
        sa.Column('error', sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ['endpoint_id'],
            ['endpoints.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_check_results_endpoint_checked_at',
        'check_results',
        ['endpoint_id', 'checked_at'],
        unique=False,
    )


def downgrade() -> None:
    """Remove the health check results table."""
    op.drop_index(
        'ix_check_results_endpoint_checked_at',
        table_name='check_results',
    )
    op.drop_table('check_results')
