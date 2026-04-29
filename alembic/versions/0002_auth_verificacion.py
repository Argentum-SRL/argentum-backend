"""dummy
Revision ID: 0002_auth_verificacion
Revises: 96760aafd2f9
Create Date: 2026-04-28 22:41:53.562741
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0002_auth_verificacion'
down_revision: Union[str, Sequence[str], None] = '96760aafd2f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
