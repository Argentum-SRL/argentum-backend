"""add_modal_bienvenida_financiera_visto

Revision ID: d8e7f6a5b4c3
Revises: f6d8d7e1a915
Create Date: 2026-09-16 22:08:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd8e7f6a5b4c3'
down_revision: Union[str, Sequence[str], None] = 'f6d8d7e1a915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('usuarios')]

    if 'modal_bienvenida_financiera_visto' not in columns:
        op.add_column(
            'usuarios',
            sa.Column('modal_bienvenida_financiera_visto', sa.Boolean(), nullable=False, server_default='false')
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('usuarios')]

    if 'modal_bienvenida_financiera_visto' in columns:
        op.drop_column('usuarios', 'modal_bienvenida_financiera_visto')
