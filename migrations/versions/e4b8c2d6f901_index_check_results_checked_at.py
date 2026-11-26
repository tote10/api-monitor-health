"""index check results by checked_at for retention cleanup

Revision ID: e4b8c2d6f901
Revises: b7e4f1a9c2d3
Create Date: 2026-11-09 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e4b8c2d6f901'
down_revision: Union[str, Sequence[str], None] = 'b7e4f1a9c2d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Support efficient pruning by check timestamp."""
    op.create_index(
        'ix_check_results_checked_at',
        'check_results',
        ['checked_at'],
        unique=False,
    )


def downgrade() -> None:
    """Remove the retention cleanup index."""
    op.drop_index(
        'ix_check_results_checked_at',
        table_name='check_results',
    )
