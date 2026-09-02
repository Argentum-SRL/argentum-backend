"""add_cotizaciones_dolar_and_trazabilidad

Revision ID: c1a2b3c4d5e6
Revises: b3c4d5e6f7a8
Create Date: 2026-09-02 20:20:00.000000

Crea la tabla cotizaciones_dolar para el almacenamiento histórico de cotizaciones
diarias (oficial, blue, tarjeta, mep) y añade las columnas de trazabilidad multimoneda
a la tabla transacciones (monto_original, moneda_original, cotizacion_aplicada,
tipo_dolar_usado).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c1a2b3c4d5e6'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Crear tabla cotizaciones_dolar
    op.create_table(
        'cotizaciones_dolar',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('tipo', sa.String(length=30), nullable=False),
        sa.Column('compra', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('venta', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('promedio', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('fecha_registro', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('fecha', 'tipo', name='uq_cotizaciones_dolar_fecha_tipo')
    )
    op.create_index(
        'ix_cotizaciones_dolar_tipo_fecha',
        'cotizaciones_dolar',
        ['tipo', 'fecha'],
        unique=False
    )

    # 2. Agregar campos de trazabilidad a transacciones
    op.add_column(
        'transacciones',
        sa.Column('monto_original', sa.Numeric(precision=15, scale=2), nullable=True)
    )
    op.add_column(
        'transacciones',
        sa.Column(
            'moneda_original',
            postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False),
            nullable=True
        )
    )
    op.add_column(
        'transacciones',
        sa.Column('cotizacion_aplicada', sa.Numeric(precision=15, scale=4), nullable=True)
    )
    op.add_column(
        'transacciones',
        sa.Column('tipo_dolar_usado', sa.String(length=30), nullable=True)
    )


def downgrade() -> None:
    # 1. Remover campos de trazabilidad de transacciones
    op.drop_column('transacciones', 'tipo_dolar_usado')
    op.drop_column('transacciones', 'cotizacion_aplicada')
    op.drop_column('transacciones', 'moneda_original')
    op.drop_column('transacciones', 'monto_original')

    # 2. Eliminar tabla cotizaciones_dolar
    op.drop_index('ix_cotizaciones_dolar_tipo_fecha', table_name='cotizaciones_dolar')
    op.drop_table('cotizaciones_dolar')
