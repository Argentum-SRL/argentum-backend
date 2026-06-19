"""rename_refresh_tokens_to_user_refresh_tokens

Revision ID: 9ca8b5d3c2e1
Revises: 8ae1d98e85be
Create Date: 2026-06-19 12:54:10.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ca8b5d3c2e1'
down_revision: Union[str, Sequence[str], None] = '8ae1d98e85be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "refresh_tokens" in tables and "user_refresh_tokens" not in tables:
        op.rename_table("refresh_tokens", "user_refresh_tokens")


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    if "user_refresh_tokens" in tables and "refresh_tokens" not in tables:
        op.rename_table("user_refresh_tokens", "refresh_tokens")
