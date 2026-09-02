"""remove_suscripciones_subcategoria

Revision ID: e8d3b2a1c4f5
Revises: e7c2a1b9f8d3
Create Date: 2026-09-02 01:25:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8d3b2a1c4f5'
down_revision: Union[str, Sequence[str], None] = 'e7c2a1b9f8d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    
    # 1. Limpiar referencias a la subcategoría 'Suscripciones' en transacciones
    conn.execute(
        sa.text("""
            UPDATE transacciones 
            SET subcategoria_id = NULL 
            WHERE subcategoria_id IN (
                SELECT s.id 
                FROM subcategorias s 
                JOIN categorias c ON s.categoria_id = c.id 
                WHERE s.nombre = 'Suscripciones' AND c.nombre = 'Recreativo'
            )
        """)
    )

    # 2. Eliminar la subcategoría global 'Suscripciones' de Recreativo
    conn.execute(
        sa.text("""
            DELETE FROM subcategorias 
            WHERE nombre = 'Suscripciones' 
              AND categoria_id IN (SELECT id FROM categorias WHERE nombre = 'Recreativo')
        """)
    )

    # 3. Reordenar las subcategorías restantes de Recreativo
    recreativo_subs = ["Salidas", "Deportes y gimnasio", "Hobbies y juegos", "Viajes"]
    for idx, sub_nombre in enumerate(recreativo_subs):
        conn.execute(
            sa.text("""
                UPDATE subcategorias 
                SET orden = :orden 
                WHERE nombre = :sub_nombre 
                  AND categoria_id IN (SELECT id FROM categorias WHERE nombre = 'Recreativo')
            """),
            {"orden": idx, "sub_nombre": sub_nombre}
        )


def downgrade() -> None:
    pass
