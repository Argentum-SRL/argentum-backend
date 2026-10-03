"""actualizar_billeteras_arq_claropay

Revision ID: a8b9c0d1e2f4
Revises: f4a1a2b3c4d5
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a8b9c0d1e2f4'
down_revision: Union[str, None] = 'f4a1a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Limpieza de tasas o claves relacionadas con Claro Pay
    op.execute(
        "DELETE FROM tasas_entidades WHERE LOWER(clave) = 'claropay' OR LOWER(clave) LIKE '%claro%pay%'"
    )
    # 2. Desasociar entidad_id 'claropay' si existiese en alguna billetera
    op.execute(
        "UPDATE billeteras SET entidad_id = NULL WHERE LOWER(entidad_id) = 'claropay'"
    )
    # 3. Eliminar billeteras huérfanas de Claro Pay sin transacciones asociadas
    op.execute(
        """
        DELETE FROM billeteras 
        WHERE LOWER(nombre) = 'claro pay' 
          AND id NOT IN (SELECT billetera_id FROM transacciones WHERE billetera_id IS NOT NULL)
          AND id NOT IN (SELECT billetera_origen_id FROM transferencias_internas WHERE billetera_origen_id IS NOT NULL)
          AND id NOT IN (SELECT billetera_destino_id FROM transferencias_internas WHERE billetera_destino_id IS NOT NULL)
        """
    )


def downgrade() -> None:
    pass
