"""add_suscripcion_id_to_transacciones

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-04 18:00:00.000000

Agrega suscripcion_id a transacciones para vincular cada cobro con su suscripción origen:
- Foreign key nullable hacia suscripciones(id) con ON DELETE SET NULL.
- Índice parcial ix_transacciones_suscripcion_id sobre suscripcion_id WHERE suscripcion_id IS NOT NULL.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'transacciones',
        sa.Column(
            'suscripcion_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('suscripciones.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index(
        'ix_transacciones_suscripcion_id',
        'transacciones',
        ['suscripcion_id'],
        postgresql_where=sa.text("suscripcion_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        'ix_transacciones_suscripcion_id',
        table_name='transacciones',
        postgresql_where=sa.text("suscripcion_id IS NOT NULL"),
    )
    op.drop_column('transacciones', 'suscripcion_id')
