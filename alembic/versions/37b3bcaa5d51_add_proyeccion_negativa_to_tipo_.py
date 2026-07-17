"""add_proyeccion_negativa_to_tipo_notificacion

Revision ID: 37b3bcaa5d51
Revises: 1d008576ffed
Create Date: 2026-07-16 20:50:14.449665

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '37b3bcaa5d51'
down_revision: Union[str, Sequence[str], None] = '1d008576ffed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agregar PROYECCION_NEGATIVA al enum tipo_notificacion_sa_enum en PostgreSQL.
    op.execute("COMMIT")
    op.execute("ALTER TYPE tipo_notificacion_sa_enum ADD VALUE IF NOT EXISTS 'PROYECCION_NEGATIVA';")


def downgrade() -> None:
    pass
