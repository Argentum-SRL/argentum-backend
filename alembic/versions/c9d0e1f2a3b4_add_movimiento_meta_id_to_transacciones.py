"""add_movimiento_meta_id_to_transacciones

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-05 18:00:00.000000

Agrega movimiento_meta_id a transacciones para vincular cada aporte o retiro con su MovimientoMeta:
- Foreign key nullable hacia movimientos_meta(id) con ON DELETE SET NULL.
- Índice parcial ix_transacciones_movimiento_meta_id sobre movimiento_meta_id WHERE movimiento_meta_id IS NOT NULL.
- Backfill seguro de transacciones históricas unívocamente vinculadas.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c9d0e1f2a3b4'
down_revision: Union[str, None] = 'b8c9d0e1f2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('transacciones')]

    if 'movimiento_meta_id' not in columns:
        op.add_column(
            'transacciones',
            sa.Column(
                'movimiento_meta_id',
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey('movimientos_meta.id', ondelete='SET NULL'),
                nullable=True,
            ),
        )

    indexes = [idx['name'] for idx in inspector.get_indexes('transacciones')]
    if 'ix_transacciones_movimiento_meta_id' not in indexes:
        op.create_index(
            'ix_transacciones_movimiento_meta_id',
            'transacciones',
            ['movimiento_meta_id'],
            postgresql_where=sa.text("movimiento_meta_id IS NOT NULL"),
        )
    # Backfill histórico seguro: vincula transacciones históricas que coinciden de manera unívoca
    op.execute(
        sa.text("""
            UPDATE transacciones t
            SET movimiento_meta_id = m.id
            FROM movimientos_meta m
            JOIN metas meta ON meta.id = m.meta_id
            WHERE t.movimiento_meta_id IS NULL
              AND t.billetera_id = m.billetera_id
              AND t.fecha = m.fecha
              AND t.monto = m.monto
              AND (
                (m.tipo = 'aporte' AND t.tipo = 'egreso' AND t.descripcion ILIKE 'Aporte a la meta: ' || meta.nombre || '%')
                OR
                (m.tipo = 'retiro' AND t.tipo = 'ingreso' AND t.descripcion ILIKE 'Retiro de la meta: ' || meta.nombre || '%')
              )
        """)
    )


def downgrade() -> None:
    op.drop_index(
        'ix_transacciones_movimiento_meta_id',
        table_name='transacciones',
        postgresql_where=sa.text("movimiento_meta_id IS NOT NULL"),
    )
    op.drop_column('transacciones', 'movimiento_meta_id')
