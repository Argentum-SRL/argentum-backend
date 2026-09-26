"""drop_columnas_vacias_perfil

Revision ID: a0f1b2c3d4e5
Revises: e9ecf9caba0f
Create Date: 2026-09-26 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Identificadores de revision usados por Alembic.
revision: str = 'a0f1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = 'e9ecf9caba0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Eliminar columnas en desuso de la tabla perfiles_financieros
    op.drop_column('perfiles_financieros', 'score_impulsividad_ars')
    op.drop_column('perfiles_financieros', 'score_impulsividad_usd')
    op.drop_column('perfiles_financieros', 'cumplimiento_presupuesto')
    op.drop_column('perfiles_financieros', 'porcentaje_suscripciones_ars')
    op.drop_column('perfiles_financieros', 'porcentaje_suscripciones_usd')

    # 2. Eliminar columnas en desuso de la tabla historial_perfiles_financieros
    op.drop_column('historial_perfiles_financieros', 'score_impulsividad_ars')
    op.drop_column('historial_perfiles_financieros', 'score_impulsividad_usd')
    op.drop_column('historial_perfiles_financieros', 'cumplimiento_presupuesto')
    op.drop_column('historial_perfiles_financieros', 'porcentaje_suscripciones_ars')
    op.drop_column('historial_perfiles_financieros', 'porcentaje_suscripciones_usd')


def downgrade() -> None:
    # 1. Recrear columnas en la tabla historial_perfiles_financieros
    op.add_column('historial_perfiles_financieros', sa.Column('porcentaje_suscripciones_usd', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('porcentaje_suscripciones_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('cumplimiento_presupuesto', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('score_impulsividad_usd', sa.Integer(), nullable=True))
    op.add_column('historial_perfiles_financieros', sa.Column('score_impulsividad_ars', sa.Integer(), nullable=True))

    # 2. Recrear columnas en la tabla perfiles_financieros
    op.add_column('perfiles_financieros', sa.Column('porcentaje_suscripciones_usd', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('porcentaje_suscripciones_ars', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('cumplimiento_presupuesto', sa.Numeric(precision=6, scale=4), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('score_impulsividad_usd', sa.Integer(), nullable=True))
    op.add_column('perfiles_financieros', sa.Column('score_impulsividad_ars', sa.Integer(), nullable=True))
