"""add_datos_template_to_notificaciones

Revision ID: eddd5847d3b5
Revises: 770e91063c86
Create Date: 2026-09-16 10:25:24.704208

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'eddd5847d3b5'
down_revision: Union[str, Sequence[str], None] = '770e91063c86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'notificaciones',
        sa.Column('datos_template', postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('notificaciones', 'datos_template')

