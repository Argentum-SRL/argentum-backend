"""drop_personal_categories_columns

Revision ID: f2a3b4c5d6e7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-02 11:30:00.000000

Etapa B2: Eliminacion definitiva de las columnas obsoletas de categorias personales
(creador_id y es_global) en las tablas categorias y subcategorias, junto con sus
indices asociados y foreign keys.

Esta migracion representa la fase de contraccion final del patron expand/contract
y requiere como condicion previa indispensable que el codigo de la aplicacion
(Etapa B1) ya se encuentre desplegado y funcionando en produccion sin referencias
a dichos campos.

Nota de downgrade:
En caso de revertir esta migracion (downgrade), la columna 'es_global' se recrea
con NOT NULL y server_default=true (estado en el que quedo tras la Etapa B1).
Esto previene que la insercion de la columna falle sobre filas existentes en la base
de datos. No se recrea ningun indice sobre 'estado' dado el volumen reducido de las
tablas (17 categorias y 57 subcategorias).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # a) Dropear los tres indices
    op.drop_index('ix_categorias_creador_id', table_name='categorias', if_exists=True)
    op.drop_index('ix_categorias_es_global_estado', table_name='categorias', if_exists=True)
    op.drop_index('ix_subcategorias_creador_id', table_name='subcategorias', if_exists=True)

    # b) Dropear las dos foreign keys
    op.drop_constraint('categorias_creador_id_fkey', 'categorias', type_='foreignkey', if_exists=True)
    op.drop_constraint('subcategorias_creador_id_fkey', 'subcategorias', type_='foreignkey', if_exists=True)

    # c) Dropear las cuatro columnas
    op.drop_column('categorias', 'creador_id', if_exists=True)
    op.drop_column('categorias', 'es_global', if_exists=True)
    op.drop_column('subcategorias', 'creador_id', if_exists=True)
    op.drop_column('subcategorias', 'es_global', if_exists=True)


def downgrade() -> None:
    # c inverso) Recrear las cuatro columnas
    op.add_column('subcategorias', sa.Column('es_global', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('subcategorias', sa.Column('creador_id', sa.UUID(), nullable=True))
    op.add_column('categorias', sa.Column('es_global', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('categorias', sa.Column('creador_id', sa.UUID(), nullable=True))

    # b inverso) Recrear las dos foreign keys
    op.create_foreign_key('subcategorias_creador_id_fkey', 'subcategorias', 'usuarios', ['creador_id'], ['id'])
    op.create_foreign_key('categorias_creador_id_fkey', 'categorias', 'usuarios', ['creador_id'], ['id'])

    # a inverso) Recrear los tres indices
    op.create_index('ix_subcategorias_creador_id', 'subcategorias', ['creador_id'], unique=False)
    op.create_index('ix_categorias_es_global_estado', 'categorias', ['es_global', 'estado'], unique=False)
    op.create_index('ix_categorias_creador_id', 'categorias', ['creador_id'], unique=False)
