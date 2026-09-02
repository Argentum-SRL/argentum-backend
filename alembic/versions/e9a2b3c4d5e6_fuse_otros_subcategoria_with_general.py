"""fuse_otros_subcategoria_with_general

Revision ID: e9a2b3c4d5e6
Revises: e8d3b2a1c4f5
Create Date: 2026-09-02 01:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e8d3b2a1c4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Fusionar transacciones que usaban la subcategoría 'Otros' hacia General (subcategoria_id = NULL)
    conn.execute(
        sa.text("""
            UPDATE transacciones 
            SET subcategoria_id = NULL 
            WHERE subcategoria_id IN (
                SELECT id FROM subcategorias WHERE nombre = 'Otros'
            )
        """)
    )

    # 2. Limpiar suscripciones que apuntaban a 'Otros'
    tables = inspector.get_table_names()
    if 'suscripciones' in tables:
        cols = [c['name'] for c in inspector.get_columns('suscripciones')]
        if 'subcategoria_id' in cols:
            conn.execute(
                sa.text("""
                    UPDATE suscripciones 
                    SET subcategoria_id = NULL 
                    WHERE subcategoria_id IN (
                        SELECT id FROM subcategorias WHERE nombre = 'Otros'
                    )
                """)
            )

    # 3. Limpiar recurrentes que apuntaban a 'Otros'
    if 'recurrentes' in tables:
        cols = [c['name'] for c in inspector.get_columns('recurrentes')]
        if 'subcategoria_id' in cols:
            conn.execute(
                sa.text("""
                    UPDATE recurrentes 
                    SET subcategoria_id = NULL 
                    WHERE subcategoria_id IN (
                        SELECT id FROM subcategorias WHERE nombre = 'Otros'
                    )
                """)
            )

    # 4. Limpiar presupuestos_categorias que apuntaban a 'Otros'
    if 'presupuestos_categorias' in tables:
        conn.execute(
            sa.text("""
                DELETE FROM presupuestos_categorias 
                WHERE subcategoria_id IN (
                    SELECT id FROM subcategorias WHERE nombre = 'Otros'
                )
            """)
        )

    # 5. Eliminar la subcategoría global 'Otros'
    conn.execute(
        sa.text("""
            DELETE FROM subcategorias 
            WHERE nombre = 'Otros' AND es_global = True
        """)
    )


def downgrade() -> None:
    pass
