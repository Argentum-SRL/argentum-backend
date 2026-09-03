"""reactivar_subcategoria_impuestos_banco

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-03 14:30:00.000000

Corrección Etapa 5: Reactivar subcategoría 'Impuestos' bajo la categoría 'Banco'
si quedó archivada por el seeder de categorías.
Idempotente: Si ya está activa, no realiza ningún cambio.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Reactivar de forma idempotente la subcategoría 'Impuestos' de la categoría 'Banco'
    op.execute(sa.text("""
        UPDATE subcategorias
        SET estado = 'activa'
        WHERE LOWER(nombre) = 'impuestos'
          AND categoria_id IN (
              SELECT id FROM categorias WHERE LOWER(nombre) = 'banco'
          )
          AND estado != 'activa';
    """))


def downgrade() -> None:
    # Revertir estado a 'archivada' para mantener consistencia inversa
    op.execute(sa.text("""
        UPDATE subcategorias
        SET estado = 'archivada'
        WHERE LOWER(nombre) = 'impuestos'
          AND categoria_id IN (
              SELECT id FROM categorias WHERE LOWER(nombre) = 'banco'
          );
    """))
