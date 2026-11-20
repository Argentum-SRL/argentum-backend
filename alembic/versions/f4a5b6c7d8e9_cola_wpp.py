# -*- coding: utf-8 -*-
"""cola durable en mensajes_whatsapp_procesados

Revision ID: f4a5b6c7d8e9
Revises: d5e6f7a8b9c1
Create Date: 2026-11-20

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Columnas de cola durable en mensajes_whatsapp_procesados (las filas existentes quedan en "listo")
    op.add_column(
        "mensajes_whatsapp_procesados",
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="listo"),
    )
    op.add_column(
        "mensajes_whatsapp_procesados",
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "mensajes_whatsapp_procesados",
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "mensajes_whatsapp_procesados",
        sa.Column("tomado_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_mensajes_wpp_procesados_estado_fecha",
        "mensajes_whatsapp_procesados",
        ["estado", "fecha_recepcion"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mensajes_wpp_procesados_estado_fecha",
        table_name="mensajes_whatsapp_procesados",
    )
    op.drop_column("mensajes_whatsapp_procesados", "tomado_en")
    op.drop_column("mensajes_whatsapp_procesados", "intentos")
    op.drop_column("mensajes_whatsapp_procesados", "payload")
    op.drop_column("mensajes_whatsapp_procesados", "estado")
