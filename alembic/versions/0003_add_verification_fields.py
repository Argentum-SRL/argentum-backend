"""add verification fields to usuarios

Revision ID: 0003_add_verification_fields
Revises: 96760aafd2f9
Create Date: 2026-04-29 00:00:00.000000

Cambios:
  - Agrega email_verificado (Boolean, default False)
  - Agrega telefono_verificado (Boolean, default False)
  - Agrega valor 'pendiente_verificacion' al enum estado_usuario_enum
  - Hace nullable: telefono, nombre, apellido
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_add_verification_fields"
down_revision = "0002_auth_verificacion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Agregar valor al enum ANTES de alterar columnas (requiere ser fuera de transacción en PG < 12)
    op.execute(sa.text("ALTER TYPE estado_usuario_enum ADD VALUE IF NOT EXISTS 'pendiente_verificacion'"))

    op.add_column(
        "usuarios",
        sa.Column("email_verificado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "usuarios",
        sa.Column("telefono_verificado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # Hacer nullable los campos que ahora pueden estar vacíos al inicio del flujo
    op.alter_column("usuarios", "telefono", existing_type=sa.String(20), nullable=True)
    op.alter_column("usuarios", "nombre", existing_type=sa.String(100), nullable=True)
    op.alter_column("usuarios", "apellido", existing_type=sa.String(100), nullable=True)


def downgrade() -> None:
    # Revertir nullability (puede fallar si hay registros con NULL)
    op.alter_column("usuarios", "apellido", existing_type=sa.String(100), nullable=False)
    op.alter_column("usuarios", "nombre", existing_type=sa.String(100), nullable=False)
    op.alter_column("usuarios", "telefono", existing_type=sa.String(20), nullable=False)

    op.drop_column("usuarios", "telefono_verificado")
    op.drop_column("usuarios", "email_verificado")

    # No se puede eliminar un valor de enum en PostgreSQL sin recrearlo
