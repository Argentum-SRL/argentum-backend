"""memoria_comercios

Revision ID: e1f2a3b4c5d6
Revises: b9c0d1e2f3a5
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'b9c0d1e2f3a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'memoria_comercios',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('usuario_id', sa.UUID(), nullable=False),
        sa.Column('clave', sa.String(length=120), nullable=False),
        sa.Column('tipo', sa.String(length=10), nullable=False),
        sa.Column('categoria_id', sa.UUID(), nullable=False),
        sa.Column('subcategoria_id', sa.UUID(), nullable=True),
        sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_actualizacion', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ),
        sa.ForeignKeyConstraint(['categoria_id'], ['categorias.id'], ),
        sa.ForeignKeyConstraint(['subcategoria_id'], ['subcategorias.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('usuario_id', 'clave', 'tipo', name='uq_memoria_comercios_usuario_clave_tipo')
    )
    op.create_index('ix_memoria_comercios_usuario_id', 'memoria_comercios', ['usuario_id'], unique=False)
    op.execute("ALTER TABLE public.memoria_comercios ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index('ix_memoria_comercios_usuario_id', table_name='memoria_comercios')
    op.drop_table('memoria_comercios')
