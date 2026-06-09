"""compat_anchor

Revision ID: db377b3256b6
Revises: 75381af94d37
Create Date: 2026-06-08 20:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'db377b3256b6'
down_revision: Union[str, Sequence[str], None] = '75381af94d37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op compatibility anchor for a missing historical revision."""


def downgrade() -> None:
    """No-op compatibility anchor for a missing historical revision."""