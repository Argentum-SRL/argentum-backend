"""add_estado_to_grupo_cuotas

Revision ID: e3d7a8f9c1b2
Revises: d01f92e84c3b
Create Date: 2026-06-19 02:19:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e3d7a8f9c1b2'
down_revision: Union[str, Sequence[str], None] = 'd01f92e84c3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Crear el tipo enum en PostgreSQL
    op.execute("CREATE TYPE estado_grupo_cuotas_enum AS ENUM ('activo', 'cancelado', 'completado')")

    # 2. Agregar la columna con default
    op.add_column('grupos_cuotas',
        sa.Column('estado', 
            postgresql.ENUM('activo', 'cancelado', 'completado', name='estado_grupo_cuotas_enum', create_type=False),
            nullable=False,
            server_default='activo'
        )
    )

    # 3. Actualizar registros existentes a 'activo'
    op.execute("UPDATE grupos_cuotas SET estado = 'activo' WHERE estado IS NULL")


def downgrade() -> None:
    # 1. Eliminar la columna
    op.drop_column('grupos_cuotas', 'estado')

    # 2. Eliminar el tipo enum
    op.execute("DROP TYPE IF EXISTS estado_grupo_cuotas_enum")
