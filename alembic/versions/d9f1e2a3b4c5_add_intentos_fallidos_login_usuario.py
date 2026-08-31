"""agregar_intentos_fallidos_login_usuario

Revision ID: d9f1e2a3b4c5
Revises: c8e1f2a3b4c5
Create Date: 2026-08-31 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9f1e2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c8e1f2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('usuarios')]

    if 'intentos_fallidos_login' not in columns:
        op.add_column(
            'usuarios',
            sa.Column('intentos_fallidos_login', sa.Integer(), nullable=False, server_default='0')
        )

    if 'ultimo_intento_fallido_at' not in columns:
        op.add_column(
            'usuarios',
            sa.Column('ultimo_intento_fallido_at', sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('usuarios')]

    if 'ultimo_intento_fallido_at' in columns:
        op.drop_column('usuarios', 'ultimo_intento_fallido_at')

    if 'intentos_fallidos_login' in columns:
        op.drop_column('usuarios', 'intentos_fallidos_login')
