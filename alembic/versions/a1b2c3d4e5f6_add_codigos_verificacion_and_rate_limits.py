"""add_codigos_verificacion_and_rate_limits

Revision ID: a1b2c3d4e5f6
Revises: 533048529c18
Create Date: 2026-09-12 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '533048529c18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "codigos_verificacion" not in tables:
        op.create_table(
            'codigos_verificacion',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column('tipo', sa.String(length=50), nullable=False),
            sa.Column('identificador', sa.String(length=255), nullable=False),
            sa.Column('codigo', sa.String(length=64), nullable=False),
            sa.Column('expiracion', sa.DateTime(timezone=True), nullable=False),
            sa.Column('intentos_fallidos', sa.Integer(), server_default='0', nullable=False),
            sa.Column('max_intentos', sa.Integer(), server_default='3', nullable=False),
            sa.Column('creado_en', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('consumido', sa.Boolean(), server_default='false', nullable=False),
            sa.Column('consumido_en', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_codigos_verificacion_codigo', 'codigos_verificacion', ['codigo'])
        op.create_index('ix_codigos_verificacion_codigo_tipo', 'codigos_verificacion', ['codigo', 'tipo'])
        op.create_index('ix_codigos_verificacion_consumido', 'codigos_verificacion', ['consumido'])
        op.create_index('ix_codigos_verificacion_expiracion', 'codigos_verificacion', ['expiracion'])
        op.create_index('ix_codigos_verificacion_identificador', 'codigos_verificacion', ['identificador'])
        op.create_index('ix_codigos_verificacion_tipo', 'codigos_verificacion', ['tipo'])
        op.create_index('ix_codigos_verificacion_tipo_identificador', 'codigos_verificacion', ['tipo', 'identificador'])

    if "rate_limits" not in tables:
        op.create_table(
            'rate_limits',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column('accion', sa.String(length=100), nullable=False),
            sa.Column('identificador', sa.String(length=255), nullable=False),
            sa.Column('cantidad', sa.Integer(), server_default='1', nullable=False),
            sa.Column('ventana_segundos', sa.Integer(), nullable=False),
            sa.Column('ventana_inicio', sa.DateTime(timezone=True), nullable=False),
            sa.Column('expira_en', sa.DateTime(timezone=True), nullable=False),
            sa.Column('detalles', sa.JSON(), nullable=True),
            sa.Column('actualizado_en', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.UniqueConstraint('accion', 'identificador', name='uq_rate_limits_accion_identificador'),
        )
        op.create_index('ix_rate_limits_accion', 'rate_limits', ['accion'])
        op.create_index('ix_rate_limits_expira_en', 'rate_limits', ['expira_en'])
        op.create_index('ix_rate_limits_identificador', 'rate_limits', ['identificador'])


def downgrade() -> None:
    op.drop_table('rate_limits')
    op.drop_table('codigos_verificacion')
