"""facturas

Revision ID: f4c2a1b2c3d4
Revises: e1f2a3b4c5d6
Create Date: 2026-10-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f4c2a1b2c3d4'
down_revision: Union[str, None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Agregar FACTURA_VENCE al enum tipo_notificacion_sa_enum en PostgreSQL
    op.execute("COMMIT")
    op.execute("ALTER TYPE tipo_notificacion_sa_enum ADD VALUE IF NOT EXISTS 'FACTURA_VENCE';")

    # 2. Tabla facturas
    op.create_table(
        'facturas',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('usuario_id', sa.UUID(), nullable=False),
        sa.Column('descripcion', sa.String(length=120), nullable=False),
        sa.Column('monto', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('moneda', postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False), nullable=False),
        sa.Column('fecha_vencimiento', sa.Date(), nullable=False),
        sa.Column('categoria_id', sa.UUID(), nullable=True),
        sa.Column('subcategoria_id', sa.UUID(), nullable=True),
        sa.Column('estado', sa.String(length=12), server_default='pendiente', nullable=False),
        sa.Column('pagada_automaticamente', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('transaccion_id', sa.UUID(), nullable=True),
        sa.Column('origen', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('monto > 0', name='chk_facturas_monto_positivo'),
        sa.CheckConstraint("estado IN ('pendiente', 'pagada', 'descartada')", name='chk_facturas_estado'),
        sa.CheckConstraint("origen IN ('whatsapp_foto', 'whatsapp_pdf')", name='chk_facturas_origen'),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['categoria_id'], ['categorias.id'], ),
        sa.ForeignKeyConstraint(['subcategoria_id'], ['subcategorias.id'], ),
        sa.ForeignKeyConstraint(['transaccion_id'], ['transacciones.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(
        'ix_facturas_usuario_estado_vencimiento',
        'facturas',
        ['usuario_id', 'estado', 'fecha_vencimiento'],
        unique=False
    )
    op.execute("ALTER TABLE public.facturas ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index('ix_facturas_usuario_estado_vencimiento', table_name='facturas')
    op.drop_table('facturas')
