"""create_perfiles_financieros

Revision ID: b3f5b72e185c
Revises: 9ca8b5d3c2e1
Create Date: 2026-06-19 17:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f5b72e185c'
down_revision: Union[str, Sequence[str], None] = '9ca8b5d3c2e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "perfiles_financieros" not in tables:
        op.create_table(
            'perfiles_financieros',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('usuario_id', sa.UUID(), nullable=False),
            sa.Column('tasa_ahorro', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('score_impulsividad', sa.Integer(), nullable=True),
            sa.Column('ratio_cuotas', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('cumplimiento_presupuesto', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('consistencia_registro', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('porcentaje_suscripciones', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('ultima_actualizacion', sa.DateTime(timezone=True), nullable=True),
            sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE'),
            sa.UniqueConstraint('usuario_id')
        )
        op.create_index('ix_perfiles_financieros_usuario_id', 'perfiles_financieros', ['usuario_id'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "perfiles_financieros" in tables:
        op.drop_index('ix_perfiles_financieros_usuario_id', table_name='perfiles_financieros')
        op.drop_table('perfiles_financieros')
