"""add_is_admin_and_reset_token_to_usuario

Revision ID: 46295108748c
Revises: d01f92e84c3b
Create Date: 2026-06-17 12:25:39.223150

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46295108748c'
down_revision: Union[str, Sequence[str], None] = 'd01f92e84c3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('usuarios', sa.Column('is_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('usuarios', sa.Column('reset_token_hash', sa.String(length=255), nullable=True))
    op.add_column('usuarios', sa.Column('reset_token_expira_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('usuarios', sa.Column('tokens_revocados_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('usuarios', 'tokens_revocados_at')
    op.drop_column('usuarios', 'reset_token_expira_at')
    op.drop_column('usuarios', 'reset_token_hash')
    op.drop_column('usuarios', 'is_admin')
