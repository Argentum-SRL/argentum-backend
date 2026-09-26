"""remove_ia_chat_from_origen_enum

Revision ID: b1c2d3e4f5a6
Revises: a0f1b2c3d4e5
Create Date: 2026-09-26 16:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Identificadores de revision usados por Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a0f1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Crear nuevo tipo enum sin el valor 'ia_chat'
    op.execute("CREATE TYPE origen_transaccion_enum_new AS ENUM ('manual', 'ia_wpp', 'ia_pdf', 'recurrente');")

    # 2. Actualizar la columna para utilizar el nuevo tipo enum
    op.execute("ALTER TABLE transacciones ALTER COLUMN origen TYPE origen_transaccion_enum_new USING origen::text::origen_transaccion_enum_new;")

    # 3. Eliminar el tipo enum anterior y renombrar el nuevo con el nombre estandar
    op.execute("DROP TYPE origen_transaccion_enum;")
    op.execute("ALTER TYPE origen_transaccion_enum_new RENAME TO origen_transaccion_enum;")


def downgrade() -> None:
    # 1. Crear tipo enum anterior incluyendo el valor 'ia_chat'
    op.execute("CREATE TYPE origen_transaccion_enum_old AS ENUM ('manual', 'ia_wpp', 'ia_chat', 'ia_pdf', 'recurrente');")

    # 2. Actualizar la columna para utilizar el tipo enum con 'ia_chat' restaurado
    op.execute("ALTER TABLE transacciones ALTER COLUMN origen TYPE origen_transaccion_enum_old USING origen::text::origen_transaccion_enum_old;")

    # 3. Eliminar el tipo enum actual y renombrar el restaurado
    op.execute("DROP TYPE origen_transaccion_enum;")
    op.execute("ALTER TYPE origen_transaccion_enum_old RENAME TO origen_transaccion_enum;")
