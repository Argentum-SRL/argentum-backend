"""create_historial_perfiles_financieros

Revision ID: c4d6e8f2a1b3
Revises: b3f5b72e185c
Create Date: 2026-06-19 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d6e8f2a1b3'
down_revision: Union[str, Sequence[str], None] = 'b3f5b72e185c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "historial_perfiles_financieros" not in tables:
        op.create_table(
            'historial_perfiles_financieros',
            sa.Column('id', sa.UUID(), nullable=False),
            sa.Column('usuario_id', sa.UUID(), nullable=False),
            sa.Column('periodo_inicio', sa.Date(), nullable=False),
            sa.Column('periodo_fin', sa.Date(), nullable=False),
            sa.Column('tasa_ahorro', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('score_impulsividad', sa.Integer(), nullable=True),
            sa.Column('ratio_cuotas', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('cumplimiento_presupuesto', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('consistencia_registro', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('porcentaje_suscripciones', sa.Numeric(precision=6, scale=4), nullable=True),
            sa.Column('fecha_snapshot', sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE')
        )
        op.create_index('ix_historial_perfiles_usuario_id', 'historial_perfiles_financieros', ['usuario_id'], unique=False)
        op.create_index('ix_historial_perfiles_periodo_inicio', 'historial_perfiles_financieros', ['periodo_inicio'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "historial_perfiles_financieros" in tables:
        op.drop_index('ix_historial_perfiles_periodo_inicio', table_name='historial_perfiles_financieros')
        op.drop_index('ix_historial_perfiles_usuario_id', table_name='historial_perfiles_financieros')
        op.drop_table('historial_perfiles_financieros')
