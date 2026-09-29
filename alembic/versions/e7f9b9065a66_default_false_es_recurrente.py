"""default_false_es_recurrente

Revision ID: e7f9b9065a66
Revises: b1c2d3e4f5a6
Create Date: 2026-09-29 11:42:13.473319

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7f9b9065a66'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('transacciones', 'es_recurrente', server_default=sa.text('false'))


def downgrade() -> None:
    op.alter_column('transacciones', 'es_recurrente', server_default=None)
