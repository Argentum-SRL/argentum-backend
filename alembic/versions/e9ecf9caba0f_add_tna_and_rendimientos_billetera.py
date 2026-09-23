"""add_tna_and_rendimientos_billetera

Revision ID: e9ecf9caba0f
Revises: 0142afd87160
Create Date: 2026-09-23 16:43:49.789437

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9ecf9caba0f'
down_revision: Union[str, Sequence[str], None] = '0142afd87160'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Columnas tna y fecha_ultimo_rendimiento en billeteras
    op.add_column('billeteras', sa.Column('tna', sa.Numeric(precision=6, scale=2), nullable=True))
    op.add_column('billeteras', sa.Column('fecha_ultimo_rendimiento', sa.DateTime(timezone=True), nullable=True))

    # 2. Tabla rendimientos_billetera
    op.create_table(
        'rendimientos_billetera',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('billetera_id', sa.UUID(), nullable=False),
        sa.Column('monto', sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column('fecha', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['billetera_id'], ['billeteras.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_rendimientos_billetera_billetera_id', 'rendimientos_billetera', ['billetera_id'], unique=False)
    op.create_index('ix_rendimientos_billetera_fecha', 'rendimientos_billetera', ['fecha'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_rendimientos_billetera_fecha', table_name='rendimientos_billetera')
    op.drop_index('ix_rendimientos_billetera_billetera_id', table_name='rendimientos_billetera')
    op.drop_table('rendimientos_billetera')
    op.drop_column('billeteras', 'fecha_ultimo_rendimiento')
    op.drop_column('billeteras', 'tna')
