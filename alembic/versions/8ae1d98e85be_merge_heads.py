"""merge_heads

Revision ID: 8ae1d98e85be
Revises: 46295108748c, e3d7a8f9c1b2
Create Date: 2026-06-19 01:13:15.263513

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8ae1d98e85be'
down_revision: Union[str, Sequence[str], None] = ('46295108748c', 'e3d7a8f9c1b2')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
