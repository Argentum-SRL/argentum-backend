"""create_importacion_tables

Revision ID: c70b3911fe61
Revises: c4d6e8f2a1b3
Create Date: 2026-07-08 18:15:40.335136

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c70b3911fe61'
down_revision: Union[str, Sequence[str], None] = 'c4d6e8f2a1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Crear los enums
    op.execute("CREATE TYPE estado_importacion_enum AS ENUM ('procesando', 'pendiente_revision', 'importado', 'error', 'cancelado')")
    op.execute("CREATE TYPE tipo_correccion_enum AS ENUM ('categoria_cambiada', 'monto_ajustado', 'fecha_ajustada', 'cuota_corregida', 'marcado_como_duplicado', 'transaccion_excluida', 'titular_reasignado')")

    # 2. Crear tabla importaciones_resumen
    op.create_table(
        'importaciones_resumen',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('usuario_id', sa.UUID(), nullable=False),
        sa.Column('admin_id', sa.UUID(), nullable=False),
        sa.Column('tarjeta_id', sa.UUID(), nullable=True),
        sa.Column('banco_detectado', sa.String(length=30), nullable=False),
        sa.Column('nombre_archivo', sa.String(length=255), nullable=False),
        sa.Column('estado', postgresql.ENUM('procesando', 'pendiente_revision', 'importado', 'error', 'cancelado', name='estado_importacion_enum', create_type=False), nullable=False, server_default='procesando'),
        sa.Column('capa_parser_usada', sa.String(length=20), nullable=True),
        sa.Column('confianza_extraccion', sa.Numeric(precision=3, scale=2), nullable=True),
        sa.Column('periodo_desde', sa.Date(), nullable=True),
        sa.Column('periodo_hasta', sa.Date(), nullable=True),
        sa.Column('titulares_detectados', postgresql.JSONB(), nullable=True),
        sa.Column('titulares_seleccionados', postgresql.JSONB(), nullable=True),
        sa.Column('transacciones_parseadas', postgresql.JSONB(), nullable=True),
        sa.Column('total_detectadas', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_importadas', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_duplicadas', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_excluidas', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('mensaje_error', sa.Text(), nullable=True),
        sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id']),
        sa.ForeignKeyConstraint(['admin_id'], ['usuarios.id']),
        sa.ForeignKeyConstraint(['tarjeta_id'], ['tarjetas_credito.id'])
    )

    # 3. Crear tabla correcciones_importacion
    op.create_table(
        'correcciones_importacion',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('importacion_id', sa.UUID(), nullable=False),
        sa.Column('banco', sa.String(length=30), nullable=False),
        sa.Column('capa_parser_usada', sa.String(length=30), nullable=False),
        sa.Column('tipo_correccion', postgresql.ENUM('categoria_cambiada', 'monto_ajustado', 'fecha_ajustada', 'cuota_corregida', 'marcado_como_duplicado', 'transaccion_excluida', 'titular_reasignado', name='tipo_correccion_enum', create_type=False), nullable=False),
        sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['importacion_id'], ['importaciones_resumen.id'], ondelete='CASCADE')
    )

    # 4. Agregar columnas a transacciones
    op.add_column('transacciones', sa.Column('import_hash', sa.String(length=64), nullable=True))
    op.add_column('transacciones', sa.Column('importacion_id', sa.UUID(), nullable=True))
    op.add_column('transacciones', sa.Column('titular_pdf', sa.String(length=150), nullable=True))
    op.create_foreign_key('fk_transacciones_importacion_id', 'transacciones', 'importaciones_resumen', ['importacion_id'], ['id'])

    # 5. Crear indices
    # 5.1 idx_transacciones_import_hash (usuario_id, import_hash) WHERE import_hash IS NOT NULL
    op.create_index('idx_transacciones_import_hash', 'transacciones', ['usuario_id', 'import_hash'], unique=True, postgresql_where='import_hash IS NOT NULL')
    # 5.2 index on transacciones.importacion_id
    op.create_index('ix_transacciones_importacion_id', 'transacciones', ['importacion_id'], unique=False)
    # 5.3 index on (banco, tipo_correccion) in correcciones_importacion
    op.create_index('ix_correcciones_importacion_banco_tipo_correccion', 'correcciones_importacion', ['banco', 'tipo_correccion'], unique=False)


def downgrade() -> None:
    # 1. Eliminar indices
    op.drop_index('ix_correcciones_importacion_banco_tipo_correccion', table_name='correcciones_importacion')
    op.drop_index('ix_transacciones_importacion_id', table_name='transacciones')
    op.drop_index('idx_transacciones_import_hash', table_name='transacciones')

    # 2. Eliminar columnas y FK de transacciones
    op.drop_constraint('fk_transacciones_importacion_id', 'transacciones', type_='foreignkey')
    op.drop_column('transacciones', 'titular_pdf')
    op.drop_column('transacciones', 'importacion_id')
    op.drop_column('transacciones', 'import_hash')

    # 3. Eliminar tablas
    op.drop_table('correcciones_importacion')
    op.drop_table('importaciones_resumen')

    # 4. Eliminar tipos enum
    op.execute("DROP TYPE IF EXISTS tipo_correccion_enum")
    op.execute("DROP TYPE IF EXISTS estado_importacion_enum")
