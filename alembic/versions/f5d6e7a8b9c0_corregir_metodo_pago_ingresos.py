"""corregir_metodo_pago_ingresos

Revision ID: f5d6e7a8b9c0
Revises: f4c2a1b2c3d4
Create Date: 2026-10-08

Corrige cualquier transacción histórica de tipo 'ingreso' cuyo metodo_pago haya sido
erróneamente asignado como 'debito' o 'credito'. Si la billetera es de efectivo se asigna
'efectivo', de lo contrario se asigna 'transferencia'.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5d6e7a8b9c0'
down_revision: Union[str, None] = 'f4c2a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE transacciones
        SET metodo_pago = CASE
            WHEN (SELECT es_efectivo FROM billeteras WHERE id = transacciones.billetera_id) = TRUE THEN 'efectivo'::metodo_pago_enum
            ELSE 'transferencia'::metodo_pago_enum
        END
        WHERE tipo = 'ingreso' AND metodo_pago IN ('debito'::metodo_pago_enum, 'credito'::metodo_pago_enum);
    """))


def downgrade() -> None:
    pass
