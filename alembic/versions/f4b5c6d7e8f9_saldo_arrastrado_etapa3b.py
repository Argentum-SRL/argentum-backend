"""saldo_arrastrado_etapa3b

Revision ID: f4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-09-02 21:30:00.000000

Etapa 3B: Modelo de Saldo Arrastrado y Amortización de Tarjetas de Crédito.
- Crea el enum estado_saldo_arrastrado_enum ('activo', 'saldado').
- Crea la tabla saldos_arrastrados_tarjeta para registrar el saldo impago de un resumen.
- Restricción uq_saldos_arrastrados_tarjeta_resumen_activo: índice parcial único
  sobre (tarjeta_id, fecha_vencimiento_resumen) donde estado = 'activo'.
- Crea la tabla pagos_saldo_arrastrado para registrar cada pago posterior que amortizó o saldó un saldo arrastrado.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f4b5c6d7e8f9'
down_revision: Union[str, None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Crear tipo ENUM para estado de saldo arrastrado
    estado_enum = postgresql.ENUM('activo', 'saldado', name='estado_saldo_arrastrado_enum')
    estado_enum.create(op.get_bind(), checkfirst=True)

    # 2. Crear tabla saldos_arrastrados_tarjeta
    op.create_table(
        'saldos_arrastrados_tarjeta',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'tarjeta_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('tarjetas_credito.id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column('fecha_vencimiento_resumen', sa.Date(), nullable=False),
        sa.Column('monto_inicial', sa.Numeric(15, 2), nullable=False),
        sa.Column('monto_restante', sa.Numeric(15, 2), nullable=False),
        sa.Column(
            'moneda',
            postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False),
            nullable=False,
            server_default='ARS'
        ),
        sa.Column(
            'estado',
            postgresql.ENUM('activo', 'saldado', name='estado_saldo_arrastrado_enum', create_type=False),
            nullable=False,
            server_default='activo'
        ),
        sa.Column(
            'transaccion_origen_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('transacciones.id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column(
            'fecha_creacion',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()')
        ),
        sa.Column(
            'fecha_modificacion',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()')
        )
    )

    # 3. Índices de saldos_arrastrados_tarjeta
    op.create_index(
        'uq_saldos_arrastrados_tarjeta_resumen_activo',
        'saldos_arrastrados_tarjeta',
        ['tarjeta_id', 'fecha_vencimiento_resumen'],
        unique=True,
        postgresql_where=sa.text("estado = 'activo'")
    )
    op.create_index(
        'ix_saldos_arrastrados_tarjeta_id',
        'saldos_arrastrados_tarjeta',
        ['tarjeta_id']
    )
    op.create_index(
        'ix_saldos_arrastrados_vencimiento',
        'saldos_arrastrados_tarjeta',
        ['fecha_vencimiento_resumen']
    )
    op.create_index(
        'ix_saldos_arrastrados_transaccion_origen',
        'saldos_arrastrados_tarjeta',
        ['transaccion_origen_id']
    )
    op.create_index(
        'ix_saldos_arrastrados_tarjeta_estado',
        'saldos_arrastrados_tarjeta',
        ['tarjeta_id', 'estado']
    )

    # 4. Crear tabla pagos_saldo_arrastrado
    op.create_table(
        'pagos_saldo_arrastrado',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'saldo_arrastrado_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('saldos_arrastrados_tarjeta.id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column(
            'transaccion_pago_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('transacciones.id', ondelete='CASCADE'),
            nullable=False
        ),
        sa.Column('monto_aplicado', sa.Numeric(15, 2), nullable=False),
        sa.Column(
            'fecha_creacion',
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text('now()')
        )
    )

    # 5. Índices de pagos_saldo_arrastrado
    op.create_index(
        'ix_pagos_saldo_arrastrado_saldo_id',
        'pagos_saldo_arrastrado',
        ['saldo_arrastrado_id']
    )
    op.create_index(
        'ix_pagos_saldo_arrastrado_tx_id',
        'pagos_saldo_arrastrado',
        ['transaccion_pago_id']
    )


def downgrade() -> None:
    # 1. Dropear pagos_saldo_arrastrado
    op.drop_index('ix_pagos_saldo_arrastrado_tx_id', table_name='pagos_saldo_arrastrado')
    op.drop_index('ix_pagos_saldo_arrastrado_saldo_id', table_name='pagos_saldo_arrastrado')
    op.drop_table('pagos_saldo_arrastrado')

    # 2. Dropear saldos_arrastrados_tarjeta
    op.drop_index('ix_saldos_arrastrados_tarjeta_estado', table_name='saldos_arrastrados_tarjeta')
    op.drop_index('ix_saldos_arrastrados_transaccion_origen', table_name='saldos_arrastrados_tarjeta')
    op.drop_index('ix_saldos_arrastrados_vencimiento', table_name='saldos_arrastrados_tarjeta')
    op.drop_index('ix_saldos_arrastrados_tarjeta_id', table_name='saldos_arrastrados_tarjeta')
    op.drop_index(
        'uq_saldos_arrastrados_tarjeta_resumen_activo',
        table_name='saldos_arrastrados_tarjeta',
        postgresql_where=sa.text("estado = 'activo'")
    )
    op.drop_table('saldos_arrastrados_tarjeta')

    # 3. Dropear enum
    sa.Enum(name='estado_saldo_arrastrado_enum').drop(op.get_bind(), checkfirst=True)
