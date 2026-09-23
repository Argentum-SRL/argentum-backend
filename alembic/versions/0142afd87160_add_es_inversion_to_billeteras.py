"""add_es_inversion_to_billeteras

Revision ID: 0142afd87160
Revises: 2704a49fdcb6
Create Date: 2026-09-23 15:39:10.657191

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0142afd87160'
down_revision: Union[str, Sequence[str], None] = '2704a49fdcb6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('billeteras', sa.Column('es_inversion', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('billeteras', 'es_inversion')
