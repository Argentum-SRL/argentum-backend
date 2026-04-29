"""add_refresh_tokens_table

Revision ID: 96760aafd2f9
Revises: 0001_initial
Create Date: 2026-04-28 22:41:53.562741

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '96760aafd2f9'
down_revision: Union[str, Sequence[str], None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema — agrega tabla refresh_tokens."""
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('usuario_id', sa.UUID(), nullable=False),
        sa.Column('token', sa.String(length=500), nullable=False),
        sa.Column('fecha_expiracion', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revocado', sa.Boolean(), nullable=False),
        sa.Column('device_info', sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token'),
    )


def downgrade() -> None:
    """Downgrade schema — elimina tabla refresh_tokens."""
    op.drop_table('refresh_tokens')
