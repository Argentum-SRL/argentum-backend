"""add_index_transacciones_usuario_fecha_creacion

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-03 17:00:00.000000

Índice de performance para detección de duplicados en transacciones por usuario y fecha de creación.
Optimiza la búsqueda de transacciones dentro de la ventana de 1 hora.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_transacciones_usuario_fecha_creacion',
        'transacciones',
        ['usuario_id', sa.text('fecha_creacion DESC')],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_transacciones_usuario_fecha_creacion', table_name='transacciones')
