"""drop_es_recurrente

Revision ID: 1cc22129905c
Revises: e7f9b9065a66
Create Date: 2026-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1cc22129905c'
down_revision: Union[str, Sequence[str], None] = 'e7f9b9065a66'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('transacciones', 'es_recurrente')


def downgrade() -> None:
    op.add_column('transacciones', sa.Column('es_recurrente', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.execute("UPDATE transacciones SET es_recurrente = (suscripcion_id IS NOT NULL)")
