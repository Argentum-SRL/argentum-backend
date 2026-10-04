"""renombrar_billeteras_efectivo_pesos_dolares

Revision ID: b9c0d1e2f3a5
Revises: a8b9c0d1e2f4
Create Date: 2026-10-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b9c0d1e2f3a5'
down_revision: Union[str, None] = 'a8b9c0d1e2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE billeteras
        SET nombre = 'Efectivo Pesos'
        WHERE es_efectivo = true
          AND (nombre = 'Efectivo ARS' OR (moneda = 'ARS' AND nombre ILIKE '%efectivo%ars%'));
        """
    )
    op.execute(
        """
        UPDATE billeteras
        SET nombre = 'Efectivo Dólares'
        WHERE es_efectivo = true
          AND (nombre = 'Efectivo USD' OR (moneda = 'USD' AND nombre ILIKE '%efectivo%usd%'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE billeteras
        SET nombre = 'Efectivo ARS'
        WHERE es_efectivo = true AND nombre = 'Efectivo Pesos';
        """
    )
    op.execute(
        """
        UPDATE billeteras
        SET nombre = 'Efectivo USD'
        WHERE es_efectivo = true AND nombre = 'Efectivo Dólares';
        """
    )
