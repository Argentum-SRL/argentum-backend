"""add_apodo_to_tarjetas_credito

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-04 01:00:00.000000

Agrega el campo apodo a la tabla tarjetas_credito para permitir identificar
tarjetas por un nombre corto personalizado en WhatsApp y la web.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tarjetas_credito', sa.Column('apodo', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('tarjetas_credito', 'apodo')
