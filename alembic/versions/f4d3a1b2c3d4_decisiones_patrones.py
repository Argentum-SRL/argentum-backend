"""decisiones_patrones

Revision ID: f4d3a1b2c3d4
Revises: a1b2c3d4e5f7
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4d3a1b2c3d4'
down_revision: Union[str, None] = 'a1b2c3d4e5f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'decisiones_patrones',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('usuario_id', sa.UUID(), nullable=False),
        sa.Column('clave_item', sa.String(length=200), nullable=False),
        sa.Column('decision', sa.String(length=12), nullable=False),
        sa.Column('caja_destino', sa.String(length=12), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("decision IN ('confirmado', 'descartado', 'movido')", name='chk_decisiones_patrones_decision'),
        sa.CheckConstraint("caja_destino IS NULL OR caja_destino IN ('fijo', 'costumbre', 'dia_a_dia')", name='chk_decisiones_patrones_caja_destino'),
        sa.CheckConstraint("(decision = 'movido' AND caja_destino IS NOT NULL) OR (decision != 'movido' AND caja_destino IS NULL)", name='chk_decisiones_patrones_caja_destino_movido'),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('usuario_id', 'clave_item', name='uq_decisiones_patrones_usuario_clave')
    )
    op.create_index('ix_decisiones_patrones_usuario_id', 'decisiones_patrones', ['usuario_id'], unique=False)
    op.execute("ALTER TABLE public.decisiones_patrones ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index('ix_decisiones_patrones_usuario_id', table_name='decisiones_patrones')
    op.drop_table('decisiones_patrones')
