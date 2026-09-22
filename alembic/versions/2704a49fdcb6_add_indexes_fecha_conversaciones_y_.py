"""add_indexes_fecha_conversaciones_y_mensajes_wpp

Revision ID: 2704a49fdcb6
Revises: c2f8a1b9e3d4
Create Date: 2026-09-22 12:50:55.811022

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2704a49fdcb6'
down_revision: Union[str, Sequence[str], None] = 'c2f8a1b9e3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'ix_conversaciones_wpp_fecha',
        'conversaciones_wpp',
        ['fecha'],
        unique=False,
    )
    op.create_index(
        'ix_mensajes_whatsapp_procesados_fecha_recepcion',
        'mensajes_whatsapp_procesados',
        ['fecha_recepcion'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_mensajes_whatsapp_procesados_fecha_recepcion', table_name='mensajes_whatsapp_procesados')
    op.drop_index('ix_conversaciones_wpp_fecha', table_name='conversaciones_wpp')
