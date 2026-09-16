"""add_index_conversaciones_wpp_usuario_fecha

Revision ID: e9962d962f76
Revises: eddd5847d3b5
Create Date: 2026-09-16 16:03:10.183439

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9962d962f76'
down_revision: Union[str, Sequence[str], None] = 'eddd5847d3b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_conversaciones_wpp_usuario_fecha',
        'conversaciones_wpp',
        ['usuario_id', sa.text('fecha DESC')],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_conversaciones_wpp_usuario_fecha', table_name='conversaciones_wpp')
