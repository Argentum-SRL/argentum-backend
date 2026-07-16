"""redesign_ipc_cache

Revision ID: 63250a724471
Revises: c70b3911fe61
Create Date: 2026-07-12 16:17:51.739573

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '63250a724471'
down_revision: Union[str, Sequence[str], None] = 'c70b3911fe61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Empty the existing duplicate/corrupt data
    op.execute("DELETE FROM ipc_cache")
    
    # 2. Rename the column valor_mensual to_indice_acumulado
    op.alter_column('ipc_cache', 'valor_mensual', new_column_name='indice_acumulado')
    
    # 3. Create unique constraint on fecha_dato
    op.create_unique_constraint('uq_ipc_cache_fecha_dato', 'ipc_cache', ['fecha_dato'])


def downgrade() -> None:
    """Downgrade schema."""
    # 1. Drop the unique constraint on fecha_dato
    op.drop_constraint('uq_ipc_cache_fecha_dato', 'ipc_cache', type_='unique')
    
    # 2. Rename the column back
    op.alter_column('ipc_cache', 'indice_acumulado', new_column_name='valor_mensual')
