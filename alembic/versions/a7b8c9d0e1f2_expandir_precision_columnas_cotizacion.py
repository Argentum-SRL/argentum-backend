"""expandir_precision_columnas_cotizacion

Revision ID: a7b8c9d0e1f2
Revises: e5f6a7b8c9d0
Create Date: 2026-09-04 12:00:00.000000

Expande la precisión de columnas de cotización para evitar desbordamiento por inflación:
- movimientos_meta.cotizacion_usada: Numeric(10, 4) -> Numeric(15, 4)
- cotizaciones_dolar.compra: Numeric(12, 4) -> Numeric(15, 4)
- cotizaciones_dolar.venta: Numeric(12, 4) -> Numeric(15, 4)
- cotizaciones_dolar.promedio: Numeric(12, 4) -> Numeric(15, 4)
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'movimientos_meta',
        'cotizacion_usada',
        existing_type=sa.Numeric(precision=10, scale=4),
        type_=sa.Numeric(precision=15, scale=4),
        existing_nullable=True,
    )
    op.alter_column(
        'cotizaciones_dolar',
        'compra',
        existing_type=sa.Numeric(precision=12, scale=4),
        type_=sa.Numeric(precision=15, scale=4),
        existing_nullable=False,
    )
    op.alter_column(
        'cotizaciones_dolar',
        'venta',
        existing_type=sa.Numeric(precision=12, scale=4),
        type_=sa.Numeric(precision=15, scale=4),
        existing_nullable=False,
    )
    op.alter_column(
        'cotizaciones_dolar',
        'promedio',
        existing_type=sa.Numeric(precision=12, scale=4),
        type_=sa.Numeric(precision=15, scale=4),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        'cotizaciones_dolar',
        'promedio',
        existing_type=sa.Numeric(precision=15, scale=4),
        type_=sa.Numeric(precision=12, scale=4),
        existing_nullable=False,
    )
    op.alter_column(
        'cotizaciones_dolar',
        'venta',
        existing_type=sa.Numeric(precision=15, scale=4),
        type_=sa.Numeric(precision=12, scale=4),
        existing_nullable=False,
    )
    op.alter_column(
        'cotizaciones_dolar',
        'compra',
        existing_type=sa.Numeric(precision=15, scale=4),
        type_=sa.Numeric(precision=12, scale=4),
        existing_nullable=False,
    )
    op.alter_column(
        'movimientos_meta',
        'cotizacion_usada',
        existing_type=sa.Numeric(precision=15, scale=4),
        type_=sa.Numeric(precision=10, scale=4),
        existing_nullable=True,
    )
