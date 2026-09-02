"""backfill_metodo_pago_transacciones

Revision ID: b3c4d5e6f7a8
Revises: f2a3b4c5d6e7
Create Date: 2026-09-02 11:40:00.000000

Esta migración es segura de ejecutar antes o después de desplegar el nuevo código
de la aplicación, ya que solo completa valores 'efectivo', 'debito' o 'credito'
en registros históricos con metodo_pago = NULL, los cuales son valores válidos
admitidos por el enum metodo_pago_enum y tolerados por todas las versiones del backend.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'f2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Relleno idempotente de metodo_pago para transacciones con valor NULL:
    # 1. Si tarjeta_id IS NOT NULL -> 'credito'
    # 2. Si billetera.es_efectivo = TRUE -> 'efectivo'
    # 3. En cualquier otro caso (o sin billetera asociada) -> 'debito'
    op.execute(sa.text("""
        UPDATE transacciones
        SET metodo_pago = CASE
            WHEN tarjeta_id IS NOT NULL THEN 'credito'::metodo_pago_enum
            WHEN (SELECT es_efectivo FROM billeteras WHERE id = transacciones.billetera_id) = TRUE THEN 'efectivo'::metodo_pago_enum
            ELSE 'debito'::metodo_pago_enum
        END
        WHERE metodo_pago IS NULL;
    """))


def downgrade() -> None:
    # Revertir un relleno de datos históricos volvería a dejar metodo_pago en NULL
    # e introduciría nuevamente inconsistencias en los filtros del dashboard.
    pass
