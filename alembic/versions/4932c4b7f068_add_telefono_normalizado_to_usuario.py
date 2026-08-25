"""add_telefono_normalizado_to_usuario

Revision ID: 4932c4b7f068
Revises: 9a8b7c6d5e4f
Create Date: 2026-08-24 21:35:42.112179

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4932c4b7f068'
down_revision: Union[str, Sequence[str], None] = '9a8b7c6d5e4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalizar_telefono_ar(telefono: str | None) -> str | None:
    if not telefono:
        return None
    digitos = "".join(c for c in str(telefono) if c.isdigit())
    if digitos.startswith("54"):
        digitos = digitos[2:]
    if digitos.startswith("9"):
        digitos = digitos[1:]
    if digitos.startswith("0"):
        digitos = digitos[1:]
    return digitos or None


def upgrade() -> None:
    # 1. Agregar columna e índice
    op.add_column('usuarios', sa.Column('telefono_normalizado', sa.String(length=20), nullable=True))
    op.create_index(op.f('ix_usuarios_telefono_normalizado'), 'usuarios', ['telefono_normalizado'], unique=False)

    # 2. Backfill de usuarios existentes
    conn = op.get_bind()
    usuarios_table = sa.table(
        'usuarios',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('telefono', sa.String),
        sa.column('telefono_normalizado', sa.String),
    )

    rows = conn.execute(
        sa.select(usuarios_table.c.id, usuarios_table.c.telefono).where(
            usuarios_table.c.telefono.isnot(None)
        )
    ).fetchall()

    for row in rows:
        norm = _normalizar_telefono_ar(row.telefono)
        if norm:
            conn.execute(
                usuarios_table.update()
                .where(usuarios_table.c.id == row.id)
                .values(telefono_normalizado=norm)
            )


def downgrade() -> None:
    op.drop_index(op.f('ix_usuarios_telefono_normalizado'), table_name='usuarios')
    op.drop_column('usuarios', 'telefono_normalizado')
