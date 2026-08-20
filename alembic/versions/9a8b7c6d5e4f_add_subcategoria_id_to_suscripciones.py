"""add_subcategoria_id_to_suscripciones

Revision ID: 9a8b7c6d5e4f
Revises: 5283ae48967c
Create Date: 2026-08-20 14:18:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a8b7c6d5e4f'
down_revision: Union[str, Sequence[str], None] = '5283ae48967c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('suscripciones', sa.Column('subcategoria_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_suscripciones_subcategoria_id',
        'suscripciones',
        'subcategorias',
        ['subcategoria_id'],
        ['id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_suscripciones_subcategoria_id', 'suscripciones', type_='foreignkey')
    op.drop_column('suscripciones', 'subcategoria_id')
