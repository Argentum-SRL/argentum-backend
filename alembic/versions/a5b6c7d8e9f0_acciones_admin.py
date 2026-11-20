# -*- coding: utf-8 -*-
"""registro de acciones de administracion

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-11-20

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "acciones_admin",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "fecha",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("admin_nombre", sa.String(length=120), nullable=False),
        sa.Column("accion", sa.String(length=40), nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("usuario_texto", sa.String(length=160), nullable=True),
        sa.Column("detalle", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["admin_id"], ["usuarios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_acciones_admin_fecha", "acciones_admin", ["fecha"], unique=False)
    op.create_index("ix_acciones_admin_accion", "acciones_admin", ["accion"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_acciones_admin_accion", table_name="acciones_admin")
    op.drop_index("ix_acciones_admin_fecha", table_name="acciones_admin")
    op.drop_table("acciones_admin")
