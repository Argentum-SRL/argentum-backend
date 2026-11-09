# -*- coding: utf-8 -*-
"""consejos

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-05

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tabla consejos_emitidos
    op.create_table(
        "consejos_emitidos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("clave", sa.String(length=120), nullable=False),
        sa.Column("moneda", sa.String(length=3), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("accion", sa.String(length=30), nullable=True),
        sa.Column("accion_datos", sa.JSON(), nullable=True),
        sa.Column("valores", sa.JSON(), nullable=False),
        sa.Column("fecha_emision", sa.Date(), nullable=False),
        sa.Column("fecha_vence", sa.Date(), nullable=False),
        sa.Column("respuesta", sa.String(length=12), nullable=True),
        sa.Column("fecha_respuesta", sa.DateTime(timezone=True), nullable=True),
        sa.Column("medicion", sa.JSON(), nullable=True),
        sa.Column("fecha_medicion", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("usuario_id", "tipo", "clave", name="uq_consejos_emitidos_usuario_tipo_clave"),
    )
    op.create_index(
        "ix_consejos_emitidos_usuario_fecha",
        "consejos_emitidos",
        ["usuario_id", "fecha_emision"],
        unique=False,
    )
    op.execute("ALTER TABLE public.consejos_emitidos ENABLE ROW LEVEL SECURITY")

    # 2. Tabla consejos_silenciados
    op.create_table(
        "consejos_silenciados",
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("usuario_id", "tipo"),
    )
    op.execute("ALTER TABLE public.consejos_silenciados ENABLE ROW LEVEL SECURITY")

    # 3. Columna usuarios.prioridades_consejos
    op.add_column("usuarios", sa.Column("prioridades_consejos", sa.JSON(), nullable=True))


def downgrade() -> None:
    # 3. Borrar columna usuarios.prioridades_consejos
    op.drop_column("usuarios", "prioridades_consejos")

    # 2. Borrar tabla consejos_silenciados
    op.drop_table("consejos_silenciados")

    # 1. Borrar tabla consejos_emitidos
    op.drop_index("ix_consejos_emitidos_usuario_fecha", table_name="consejos_emitidos")
    op.drop_table("consejos_emitidos")

