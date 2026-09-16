"""remove_transacciones_recurrentes_and_column

Revision ID: f6d8d7e1a915
Revises: e9962d962f76
Create Date: 2026-09-16 18:04:57.214005

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f6d8d7e1a915'
down_revision: Union[str, Sequence[str], None] = 'e9962d962f76'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: remove transacciones_recurrentes table and transacciones.recurrente_id column."""
    # 1. Eliminar FK e índice en transacciones
    op.execute("DROP INDEX IF EXISTS ix_transacciones_recurrente_fecha")
    op.drop_constraint('transacciones_recurrente_id_fkey', 'transacciones', type_='foreignkey')
    op.drop_column('transacciones', 'recurrente_id')

    # 2. Eliminar índices y tabla transacciones_recurrentes
    op.execute("DROP INDEX IF EXISTS ix_transacciones_recurrentes_billetera_id")
    op.execute("DROP INDEX IF EXISTS ix_transacciones_recurrentes_estado")
    op.execute("DROP INDEX IF EXISTS ix_transacciones_recurrentes_usuario_id")
    op.drop_table('transacciones_recurrentes')

    # 3. Eliminar tipos enum huérfanos si existen
    op.execute("DROP TYPE IF EXISTS tipo_transaccion_recurrente_enum")
    op.execute("DROP TYPE IF EXISTS frecuencia_transaccion_recurrente_enum")
    op.execute("DROP TYPE IF EXISTS estado_transaccion_recurrente_enum")


def downgrade() -> None:
    """Downgrade schema."""
    # Recrear tipos enum de forma segura
    op.execute("DO $$ BEGIN CREATE TYPE tipo_transaccion_recurrente_enum AS ENUM ('ingreso', 'egreso'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE frecuencia_transaccion_recurrente_enum AS ENUM ('semanal', 'quincenal', 'mensual'); EXCEPTION WHEN duplicate_object THEN null; END $$;")
    op.execute("DO $$ BEGIN CREATE TYPE estado_transaccion_recurrente_enum AS ENUM ('activa', 'pausada'); EXCEPTION WHEN duplicate_object THEN null; END $$;")

    tipo_enum = postgresql.ENUM('ingreso', 'egreso', name='tipo_transaccion_recurrente_enum', create_type=False)
    frec_enum = postgresql.ENUM('semanal', 'quincenal', 'mensual', name='frecuencia_transaccion_recurrente_enum', create_type=False)
    estado_enum = postgresql.ENUM('activa', 'pausada', name='estado_transaccion_recurrente_enum', create_type=False)
    moneda_enum = postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False)

    # Recrear tabla
    op.create_table('transacciones_recurrentes',
        sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('usuario_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('tipo', tipo_enum, autoincrement=False, nullable=False),
        sa.Column('monto', sa.NUMERIC(precision=15, scale=2), autoincrement=False, nullable=False),
        sa.Column('moneda', moneda_enum, autoincrement=False, nullable=False),
        sa.Column('descripcion', sa.VARCHAR(length=200), autoincrement=False, nullable=False),
        sa.Column('categoria_id', sa.UUID(), autoincrement=False, nullable=True),
        sa.Column('subcategoria_id', sa.UUID(), autoincrement=False, nullable=True),
        sa.Column('billetera_id', sa.UUID(), autoincrement=False, nullable=False),
        sa.Column('frecuencia', frec_enum, autoincrement=False, nullable=False),
        sa.Column('dia_registro', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('estado', estado_enum, autoincrement=False, nullable=False),
        sa.Column('fecha_creacion', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(['billetera_id'], ['billeteras.id'], name=op.f('transacciones_recurrentes_billetera_id_fkey')),
        sa.ForeignKeyConstraint(['categoria_id'], ['categorias.id'], name=op.f('transacciones_recurrentes_categoria_id_fkey')),
        sa.ForeignKeyConstraint(['subcategoria_id'], ['subcategorias.id'], name=op.f('transacciones_recurrentes_subcategoria_id_fkey')),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], name=op.f('transacciones_recurrentes_usuario_id_fkey')),
        sa.PrimaryKeyConstraint('id', name=op.f('transacciones_recurrentes_pkey'))
    )
    op.create_index(op.f('ix_transacciones_recurrentes_usuario_id'), 'transacciones_recurrentes', ['usuario_id'], unique=False)
    op.create_index(op.f('ix_transacciones_recurrentes_estado'), 'transacciones_recurrentes', ['estado'], unique=False)
    op.create_index(op.f('ix_transacciones_recurrentes_billetera_id'), 'transacciones_recurrentes', ['billetera_id'], unique=False)

    # Recrear columna en transacciones
    op.add_column('transacciones', sa.Column('recurrente_id', sa.UUID(), autoincrement=False, nullable=True))
    op.create_foreign_key(op.f('transacciones_recurrente_id_fkey'), 'transacciones', 'transacciones_recurrentes', ['recurrente_id'], ['id'])
    op.create_index(op.f('ix_transacciones_recurrente_fecha'), 'transacciones', ['recurrente_id', 'fecha'], unique=False, postgresql_where='(recurrente_id IS NOT NULL)')
