"""crear_tabla_analisis_ia

Revision ID: e6d5c4b3a2f1
Revises: c4d6e8f2a1b3
Create Date: 2026-06-24 20:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e6d5c4b3a2f1'
down_revision: Union[str, Sequence[str], None] = 'c4d6e8f2a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = 'c4d6e8f2a1b3'


def upgrade() -> None:
    # 1. Crear tabla sin IF NOT EXISTS para que falle ruidosamente si ya existe.
    op.create_table(
        'analisis_ia',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('usuario_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('usuarios.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tipo_analisis', sa.String(length=30), nullable=False, server_default='completo'),
        sa.Column('ciclos_analizados', sa.Integer(), nullable=False),
        sa.Column('periodo_inicio', sa.Date(), nullable=False),
        sa.Column('periodo_fin', sa.Date(), nullable=False),
        sa.Column('perfil_detectado', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('payload_enviado', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('resultado', sa.Text(), nullable=True),
        sa.Column('resultado_secciones', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('estado', sa.String(length=20), nullable=False, server_default='pendiente'),
        sa.Column('error_detalle', sa.Text(), nullable=True),
        sa.Column('modelo_usado', sa.String(length=50), nullable=False, server_default='gpt-4o-mini'),
        sa.Column('input_tokens', sa.Integer(), nullable=True),
        sa.Column('output_tokens', sa.Integer(), nullable=True),
        sa.Column('costo_usd', sa.Numeric(precision=10, scale=6), nullable=True),
        sa.Column('generado_por', sa.String(length=20), nullable=False, server_default='manual'),
        sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())
    )

    # 2. Crear índices estándar no-concurrentes
    op.create_index('ix_analisis_ia_usuario_id', 'analisis_ia', ['usuario_id'])
    op.create_index('ix_analisis_ia_tipo_analisis', 'analisis_ia', ['tipo_analisis'])

    # 3. Crear índices concurrentes y especiales usando op.execute
    # Cerramos la transacción actual para evitar bloqueos
    op.execute("COMMIT")
    try:
        # Índice compuesto para límite de uso
        op.execute("CREATE INDEX CONCURRENTLY ix_analisis_ia_usuario_periodo_tipo ON analisis_ia (usuario_id, periodo_fin, tipo_analisis)")
        # Índice DESC para ordenamiento de historial
        op.execute("CREATE INDEX CONCURRENTLY ix_analisis_ia_creado_en_desc ON analisis_ia (creado_en DESC)")
    finally:
        # Reabrimos transacción requerida por Alembic para finalizar
        op.execute("BEGIN")


def downgrade() -> None:
    # 1. Eliminar índices especiales concurrentemente
    op.execute("COMMIT")
    try:
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_analisis_ia_usuario_periodo_tipo")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_analisis_ia_creado_en_desc")
    finally:
        op.execute("BEGIN")

    # 2. Eliminar índices estándar
    op.drop_index('ix_analisis_ia_tipo_analisis', table_name='analisis_ia')
    op.drop_index('ix_analisis_ia_usuario_id', table_name='analisis_ia')

    # 3. Eliminar tabla
    op.drop_table('analisis_ia')
