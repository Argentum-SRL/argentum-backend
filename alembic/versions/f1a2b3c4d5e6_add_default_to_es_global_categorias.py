"""add_default_to_es_global_categorias

Revision ID: f1a2b3c4d5e6
Revises: e9a2b3c4d5e6
Create Date: 2026-09-02 10:35:00.000000

Etapa B1: Asignar server_default true a la columna es_global en categorias y subcategorias.
En PostgreSQL, 'ALTER TABLE ... ALTER COLUMN ... SET DEFAULT' es una operacion puramente
de catalogo/metadatos: NO reescribe la tabla, toma un lock ACCESS EXCLUSIVE instantaneo
(microsegundos) y no bloquea el trafico concurrente de produccion en Railway/Supabase.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e9a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'categorias',
        'es_global',
        server_default=sa.text('true'),
        existing_type=sa.Boolean(),
        existing_nullable=False
    )
    op.alter_column(
        'subcategorias',
        'es_global',
        server_default=sa.text('true'),
        existing_type=sa.Boolean(),
        existing_nullable=False
    )


def downgrade() -> None:
    op.alter_column(
        'categorias',
        'es_global',
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False
    )
    op.alter_column(
        'subcategorias',
        'es_global',
        server_default=None,
        existing_type=sa.Boolean(),
        existing_nullable=False
    )
