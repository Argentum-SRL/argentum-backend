"""add_resumen_ciclo_to_tipo_notificacion

Revision ID: d01f92e84c3b
Revises: f8a6b4c3d2e1
Create Date: 2026-06-14 17:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd01f92e84c3b'
down_revision: Union[str, Sequence[str], None] = 'f8a6b4c3d2e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agregar RESUMEN_CICLO al enum tipo_notificacion_sa_enum en PostgreSQL.
    # ALTER TYPE ... ADD VALUE no puede ejecutarse dentro de un bloque de transacciones.
    # Alembic envuelve las migraciones en transacciones por defecto, así que commiteamos primero.
    op.execute("COMMIT")
    op.execute("ALTER TYPE tipo_notificacion_sa_enum ADD VALUE IF NOT EXISTS 'RESUMEN_CICLO';")


def downgrade() -> None:
    # No se puede eliminar fácilmente un valor de un tipo ENUM en PostgreSQL sin recrear todo el tipo enum.
    # Se deja vacío para evitar riesgos de consistencia de datos.
    pass
