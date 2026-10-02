"""ajustes_saldo_y_tasas

Revision ID: f4a1a2b3c4d5
Revises: 1cc22129905c
Create Date: 2026-10-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f4a1a2b3c4d5'
down_revision: Union[str, None] = '1cc22129905c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tabla ajustes_saldo
    op.create_table(
        'ajustes_saldo',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('billetera_id', sa.UUID(), nullable=False),
        sa.Column('monto', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('saldo_anterior', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('saldo_declarado', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['billetera_id'], ['billeteras.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ajustes_saldo_billetera_fecha', 'ajustes_saldo', ['billetera_id', 'fecha'], unique=False)
    op.execute("ALTER TABLE public.ajustes_saldo ENABLE ROW LEVEL SECURITY")

    # 2. Tabla tasas_entidades
    op.create_table(
        'tasas_entidades',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('fuente', sa.String(length=30), nullable=False),
        sa.Column('clave', sa.String(length=120), nullable=False),
        sa.Column('tna', sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column('tope', sa.Numeric(precision=15, scale=2), nullable=True),
        sa.Column('condiciones', sa.String(length=300), nullable=True),
        sa.Column('fecha_dato', sa.Date(), nullable=False),
        sa.Column('vcp', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('vcp_anterior', sa.Numeric(precision=20, scale=6), nullable=True),
        sa.Column('fecha_dato_anterior', sa.Date(), nullable=True),
        sa.Column('fecha_consulta', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fuente', 'clave', 'fecha_dato', name='uq_tasas_entidades_fuente_clave_fecha')
    )
    op.create_index('ix_tasas_entidades_clave_fecha', 'tasas_entidades', ['clave', 'fecha_dato'], unique=False)
    op.execute("ALTER TABLE public.tasas_entidades ENABLE ROW LEVEL SECURITY")

    # 3. Columnas en billeteras
    op.add_column('billeteras', sa.Column('entidad_id', sa.String(length=50), nullable=True))
    op.add_column('billeteras', sa.Column('nivel_tasa', sa.String(length=120), nullable=True))


def downgrade() -> None:
    # 3. Columnas en billeteras
    op.drop_column('billeteras', 'nivel_tasa')
    op.drop_column('billeteras', 'entidad_id')

    # 2. Tabla tasas_entidades
    op.drop_index('ix_tasas_entidades_clave_fecha', table_name='tasas_entidades')
    op.drop_table('tasas_entidades')

    # 1. Tabla ajustes_saldo
    op.drop_index('ix_ajustes_saldo_billetera_fecha', table_name='ajustes_saldo')
    op.drop_table('ajustes_saldo')
