"""add_notification_channel_preferences_for_5_types

Revision ID: 5283ae48967c
Revises: 37b3bcaa5d51
Create Date: 2026-07-16 21:03:56.074066

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
"""add_notification_channel_preferences_for_5_types

Revision ID: 5283ae48967c
Revises: 37b3bcaa5d51
Create Date: 2026-07-16 21:03:56.074066

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5283ae48967c'
down_revision: Union[str, Sequence[str], None] = '37b3bcaa5d51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('configuracion_notificaciones', sa.Column('saldo_cero_activo', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('configuracion_notificaciones', sa.Column('resumen_ciclo_activo', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('configuracion_notificaciones', sa.Column('resumen_ciclo_web', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('configuracion_notificaciones', sa.Column('resumen_ciclo_whatsapp', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('configuracion_notificaciones', sa.Column('proyeccion_negativa_activo', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('configuracion_notificaciones', sa.Column('proyeccion_negativa_web', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('configuracion_notificaciones', sa.Column('proyeccion_negativa_whatsapp', sa.Boolean(), nullable=False, server_default=sa.text('true')))


def downgrade() -> None:
    op.drop_column('configuracion_notificaciones', 'proyeccion_negativa_whatsapp')
    op.drop_column('configuracion_notificaciones', 'proyeccion_negativa_web')
    op.drop_column('configuracion_notificaciones', 'proyeccion_negativa_activo')
    op.drop_column('configuracion_notificaciones', 'resumen_ciclo_whatsapp')
    op.drop_column('configuracion_notificaciones', 'resumen_ciclo_web')
    op.drop_column('configuracion_notificaciones', 'resumen_ciclo_activo')
    op.drop_column('configuracion_notificaciones', 'saldo_cero_activo')
