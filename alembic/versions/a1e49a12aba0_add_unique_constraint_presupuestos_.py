"""add_unique_constraint_presupuestos_categorias

Revision ID: a1e49a12aba0
Revises: 4932c4b7f068
Create Date: 2026-08-25 20:29:52.788990

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1e49a12aba0'
down_revision: Union[str, Sequence[str], None] = '4932c4b7f068'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        'uq_presupuestos_categorias_presupuesto_cat_subcat',
        'presupuestos_categorias',
        ['presupuesto_id', 'categoria_id', 'subcategoria_id']
    )


def downgrade() -> None:
    op.drop_constraint(
        'uq_presupuestos_categorias_presupuesto_cat_subcat',
        'presupuestos_categorias',
        type_='unique'
    )
