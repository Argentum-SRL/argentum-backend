"""resumen impago

Revision ID: fe6f70819203
Revises: fd5e6f708192
Create Date: 2026-10-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe6f70819203'
down_revision: Union[str, Sequence[str], None] = 'fd5e6f708192'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agregar RESUMEN_IMPAGO al enum tipo_notificacion_sa_enum en PostgreSQL.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("COMMIT")
        op.execute("ALTER TYPE tipo_notificacion_sa_enum ADD VALUE IF NOT EXISTS 'RESUMEN_IMPAGO';")


def downgrade() -> None:
    pass
