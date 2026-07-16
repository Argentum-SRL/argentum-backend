"""redesign_perfil_financiero

Revision ID: 1d008576ffed
Revises: 63250a724471
Create Date: 2026-07-12 20:08:09.145499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
"""redesign_perfil_financiero

Revision ID: 1d008576ffed
Revises: 63250a724471
Create Date: 2026-07-12 20:08:09.145499

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1d008576ffed'
down_revision: Union[str, Sequence[str], None] = '63250a724471'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Truncate tables to prevent conflicts with old data
    op.execute("TRUNCATE TABLE perfiles_financieros CASCADE;")
    op.execute("TRUNCATE TABLE historial_perfiles_financieros CASCADE;")

    # Historial perfiles
    op.add_column('historial_perfiles_financieros', sa.Column('tasa_ahorro_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('tasa_ahorro_usd', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('score_impulsividad_ars', sa.Integer(), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('score_impulsividad_usd', sa.Integer(), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('ratio_cuotas_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('ratio_cuotas_usd', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('porcentaje_suscripciones_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('porcentaje_suscripciones_usd', sa.Numeric(precision=6, scale=4), nullable=True))

    op.drop_column('historial_perfiles_financieros', 'score_impulsividad')
    op.drop_column('historial_perfiles_financieros', 'ratio_cuotas')
    op.drop_column('historial_perfiles_financieros', 'porcentaje_suscripciones')
    op.drop_column('historial_perfiles_financieros', 'tasa_ahorro')

    # Perfiles
    op.add_column('perfiles_financieros', sa.Column('tasa_ahorro_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('tasa_ahorro_usd', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('score_impulsividad_ars', sa.Integer(), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('score_impulsividad_usd', sa.Integer(), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('ratio_cuotas_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('ratio_cuotas_usd', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('porcentaje_suscripciones_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('porcentaje_suscripciones_usd', sa.Numeric(precision=6, scale=4), nullable=True))

    op.drop_column('perfiles_financieros', 'score_impulsividad')
    op.drop_column('perfiles_financieros', 'ratio_cuotas')
    op.drop_column('perfiles_financieros', 'porcentaje_suscripciones')
    op.drop_column('perfiles_financieros', 'tasa_ahorro')


def downgrade() -> None:
    """Downgrade schema."""
    # Perfiles
    op.add_column('perfiles_financieros', sa.Column('tasa_ahorro', sa.NUMERIC(precision=6, scale=4), autoincrement=False, nullable=True))
    op.add_column('perfiles_financieros', sa.Column('porcentaje_suscripciones', sa.NUMERIC(precision=6, scale=4), autoincrement=False, nullable=True))
    op.add_column('perfiles_financieros', sa.Column('ratio_cuotas', sa.NUMERIC(precision=6, scale=4), autoincrement=False, nullable=True))
    op.add_column('perfiles_financieros', sa.Column('score_impulsividad', sa.INTEGER(), autoincrement=False, nullable=True))

    op.drop_column('perfiles_financieros', 'porcentaje_suscripciones_usd')
    op.drop_column('perfiles_financieros', 'porcentaje_suscripciones_ars')
    op.drop_column('perfiles_financieros', 'ratio_cuotas_usd')
    op.drop_column('perfiles_financieros', 'ratio_cuotas_ars')
    op.drop_column('perfiles_financieros', 'score_impulsividad_usd')
    op.drop_column('perfiles_financieros', 'score_impulsividad_ars')
    op.drop_column('perfiles_financieros', 'tasa_ahorro_usd')
    op.drop_column('perfiles_financieros', 'tasa_ahorro_ars')

    # Historial perfiles
    op.add_column('historial_perfiles_financieros', sa.Column('tasa_ahorro', sa.NUMERIC(precision=6, scale=4), autoincrement=False, nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('porcentaje_suscripciones', sa.NUMERIC(precision=6, scale=4), autoincrement=False, nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('ratio_cuotas', sa.NUMERIC(precision=6, scale=4), autoincrement=False, nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('score_impulsividad', sa.INTEGER(), autoincrement=False, nullable=True))

    op.drop_column('historial_perfiles_financieros', 'porcentaje_suscripciones_usd')
    op.drop_column('historial_perfiles_financieros', 'porcentaje_suscripciones_ars')
    op.drop_column('historial_perfiles_financieros', 'ratio_cuotas_usd')
    op.drop_column('historial_perfiles_financieros', 'ratio_cuotas_ars')
    op.drop_column('historial_perfiles_financieros', 'score_impulsividad_usd')
    op.drop_column('historial_perfiles_financieros', 'score_impulsividad_ars')
    op.drop_column('historial_perfiles_financieros', 'tasa_ahorro_usd')
    op.drop_column('historial_perfiles_financieros', 'tasa_ahorro_ars')
    # ### end Alembic commands ###
