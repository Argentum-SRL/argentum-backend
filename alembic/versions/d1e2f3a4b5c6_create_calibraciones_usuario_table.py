"""create_calibraciones_usuario_table

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-09-10 19:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'calibraciones_usuario',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('usuario_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('usuarios.id', ondelete='CASCADE'), nullable=False),
        sa.Column('moneda', sa.String(length=10), nullable=False),
        sa.Column('inicio_ciclo', sa.Date(), nullable=False),
        sa.Column('pasa_puerta', sa.Boolean(), nullable=False),
        sa.Column('motivo', sa.String(length=50), nullable=True),
        sa.Column('mensaje', sa.Text(), nullable=True),
        sa.Column('ciclos_evaluados', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cobertura_50', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('cobertura_80', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('cobertura_95', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('ancho_medio_80_rel', sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column('detalles', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('fecha_calculo', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duracion_ms', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.UniqueConstraint('usuario_id', 'moneda', name='uq_calibraciones_usuario_usuario_moneda')
    )
    op.create_index('ix_calibraciones_usuario_usuario_id', 'calibraciones_usuario', ['usuario_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_calibraciones_usuario_usuario_id', table_name='calibraciones_usuario')
    op.drop_table('calibraciones_usuario')
