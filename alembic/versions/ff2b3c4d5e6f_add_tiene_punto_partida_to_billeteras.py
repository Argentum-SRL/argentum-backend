"""add_tiene_punto_partida_to_billeteras

Revision ID: ff2b3c4d5e6f
Revises: ff1a2b3c4d5e
Create Date: 2026-10-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión de Alembic.
revision: str = 'ff2b3c4d5e6f'
down_revision: Union[str, Sequence[str], None] = 'ff1a2b3c4d5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agregar columna tiene_punto_partida a billeteras (BOOLEAN NOT NULL, server_default true).
    op.add_column(
        'billeteras',
        sa.Column(
            'tiene_punto_partida',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )


def downgrade() -> None:
    op.drop_column('billeteras', 'tiene_punto_partida')
