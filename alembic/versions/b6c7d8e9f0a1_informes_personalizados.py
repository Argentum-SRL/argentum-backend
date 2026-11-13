# -*- coding: utf-8 -*-
"""informes_personalizados

Revision ID: b6c7d8e9f0a1
Revises: e3f4a5b6c7d8
Create Date: 2026-11-13

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if not insp.has_table("informes_personalizados"):
        op.create_table(
            "informes_personalizados",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "usuario_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("usuarios.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("nombre", sa.String(length=120), nullable=False),
            sa.Column("descripcion", sa.String(length=300), nullable=True),
            sa.Column("icono", sa.String(length=40), nullable=False, server_default="pie-chart"),
            sa.Column("es_favorito", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("configuracion", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("fecha_actualizacion", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index(
            "ix_informes_personalizados_usuario_id",
            "informes_personalizados",
            ["usuario_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("informes_personalizados"):
        op.drop_index("ix_informes_personalizados_usuario_id", table_name="informes_personalizados")
        op.drop_table("informes_personalizados")
