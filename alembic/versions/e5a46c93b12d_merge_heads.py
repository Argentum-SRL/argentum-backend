"""merge heads

Revision ID: e5a46c93b12d
Revises: 4ea4f7e4ec0f, 5e22b239aa5e
Create Date: 2026-06-09 20:54:24.701786

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5a46c93b12d'
down_revision: Union[str, Sequence[str], None] = ('4ea4f7e4ec0f', '5e22b239aa5e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
