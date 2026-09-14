"""alter_accion_ejecutada_type_text

Revision ID: 770e91063c86
Revises: a1b2c3d4e5f6
Create Date: 2026-09-14 18:02:56.982468

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '770e91063c86'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'conversaciones_wpp',
        'accion_ejecutada',
        existing_type=sa.VARCHAR(length=100),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'conversaciones_wpp',
        'accion_ejecutada',
        existing_type=sa.Text(),
        type_=sa.VARCHAR(length=100),
        existing_nullable=True,
    )
