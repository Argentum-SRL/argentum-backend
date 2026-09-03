"""etapa4_transferencias_bimonetarias

Revision ID: b2c3d4e5f6a7
Revises: f5c6d7e8f9a0
Create Date: 2026-09-03 13:30:00.000000

Etapa 4: Compra y venta de dólares entre billeteras propias.
- Agrega monto_origen, monto_destino, moneda_origen, moneda_destino a transferencias_internas.
- Agrega cotizacion a transferencias_internas (Numeric(15, 4)).
- Agrega transaccion_comision_id, monto_comision, moneda_comision a transferencias_internas.
- Crea índice para transaccion_comision_id.
- Backfill de registros existentes para poblar monto_origen, monto_destino, moneda_origen, moneda_destino.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'f5c6d7e8f9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Agregar columnas bimonetarias
    op.add_column(
        'transferencias_internas',
        sa.Column('monto_origen', sa.Numeric(15, 2), nullable=True)
    )
    op.add_column(
        'transferencias_internas',
        sa.Column('monto_destino', sa.Numeric(15, 2), nullable=True)
    )
    op.add_column(
        'transferencias_internas',
        sa.Column(
            'moneda_origen',
            postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False),
            nullable=True
        )
    )
    op.add_column(
        'transferencias_internas',
        sa.Column(
            'moneda_destino',
            postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False),
            nullable=True
        )
    )
    op.add_column(
        'transferencias_internas',
        sa.Column('cotizacion', sa.Numeric(15, 4), nullable=True)
    )

    # 2. Agregar columnas de comisión
    op.add_column(
        'transferencias_internas',
        sa.Column(
            'transaccion_comision_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('transacciones.id', ondelete='SET NULL'),
            nullable=True
        )
    )
    op.add_column(
        'transferencias_internas',
        sa.Column('monto_comision', sa.Numeric(15, 2), nullable=True)
    )
    op.add_column(
        'transferencias_internas',
        sa.Column(
            'moneda_comision',
            postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False),
            nullable=True
        )
    )

    # 3. Crear índice para transaccion_comision_id
    op.create_index(
        'ix_transferencias_internas_transaccion_comision_id',
        'transferencias_internas',
        ['transaccion_comision_id'],
        unique=False,
        postgresql_where=sa.text("transaccion_comision_id IS NOT NULL")
    )

    # 4. Backfill para registros existentes (mismo origen y destino)
    op.execute("""
        UPDATE transferencias_internas
        SET monto_origen = monto,
            monto_destino = monto,
            moneda_origen = moneda,
            moneda_destino = moneda
        WHERE monto_destino IS NULL;
    """)


def downgrade() -> None:
    # 1. Eliminar índice de comisión
    op.drop_index(
        'ix_transferencias_internas_transaccion_comision_id',
        table_name='transferencias_internas',
        postgresql_where=sa.text("transaccion_comision_id IS NOT NULL")
    )

    # 2. Eliminar columnas de comisión
    op.drop_column('transferencias_internas', 'moneda_comision')
    op.drop_column('transferencias_internas', 'monto_comision')
    op.drop_column('transferencias_internas', 'transaccion_comision_id')

    # 3. Eliminar columnas bimonetarias
    op.drop_column('transferencias_internas', 'cotizacion')
    op.drop_column('transferencias_internas', 'moneda_destino')
    op.drop_column('transferencias_internas', 'moneda_origen')
    op.drop_column('transferencias_internas', 'monto_destino')
    op.drop_column('transferencias_internas', 'monto_origen')
