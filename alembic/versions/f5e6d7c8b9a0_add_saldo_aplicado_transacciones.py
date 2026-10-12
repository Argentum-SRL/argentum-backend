"""add saldo_aplicado to transacciones

Revision ID: f5e6d7c8b9a0
Revises: f4d3a1b2c3d4
Create Date: 2026-10-12
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f5e6d7c8b9a0'
down_revision: Union[str, None] = 'f4d3a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Agregar columna saldo_aplicado con default true
    op.add_column(
        'transacciones',
        sa.Column('saldo_aplicado', sa.Boolean(), nullable=False, server_default=sa.text('true'))
    )

    # 2. Actualizar a false las filas que quedaron pendientes por fecha futura
    # y la transacción específica c7a734c5 de Usuario_05 (egreso de 178217.07 con fecha 2026-10-12,
    # creada originalmente el 08/10 en hora argentina sin impactar saldo al momento de creación).
    op.execute("""
        UPDATE transacciones
        SET saldo_aplicado = false
        WHERE (
            (estado_verificacion IS NULL OR estado_verificacion != 'pendiente')
            AND (metodo_pago IS NULL OR metodo_pago != 'credito')
            AND fecha > (CURRENT_TIMESTAMP AT TIME ZONE 'America/Argentina/Buenos_Aires')::date
        )
        -- Transacción c7a734c5-952e-4b66-b4ae-b8028663f9b3 creada el 08/10 con fecha de hoy que no movió saldo en su creación:
        OR id = 'c7a734c5-952e-4b66-b4ae-b8028663f9b3';
    """)


def downgrade() -> None:
    op.drop_column('transacciones', 'saldo_aplicado')
