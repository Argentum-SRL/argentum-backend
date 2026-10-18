"""add_fecha_saldo_to_billeteras

Revision ID: ff1a2b3c4d5e
Revises: fe6f70819203
Create Date: 2026-10-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión de Alembic.
revision: str = 'ff1a2b3c4d5e'
down_revision: Union[str, Sequence[str], None] = 'fe6f70819203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agregar columna fecha_saldo a billeteras (DATE, NULL).
    op.add_column('billeteras', sa.Column('fecha_saldo', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('billeteras', 'fecha_saldo')
