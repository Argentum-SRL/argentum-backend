"""add_imagen_to_tipo_mensaje_wpp

Revision ID: c2f8a1b9e3d4
Revises: d8e7f6a5b4c3
Create Date: 2026-09-17 19:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2f8a1b9e3d4'
down_revision: Union[str, Sequence[str], None] = 'd8e7f6a5b4c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE tipo_mensaje_wpp_enum ADD VALUE IF NOT EXISTS 'imagen';")


def downgrade() -> None:
    pass
