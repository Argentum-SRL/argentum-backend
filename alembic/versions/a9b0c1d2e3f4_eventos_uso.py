# -*- coding: utf-8 -*-
"""eventos_uso

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-28

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "eventos_uso" not in tables:
        op.create_table(
            "eventos_uso",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column(
                "usuario_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("usuarios.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("evento", sa.String(length=40), nullable=False),
            sa.Column("dia", sa.Date(), nullable=False),
            sa.Column(
                "fecha",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("usuario_id", "evento", "dia", name="uq_eventos_uso_usuario_evento_dia"),
        )
        op.create_index("ix_eventos_uso_evento_dia", "eventos_uso", ["evento", "dia"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "eventos_uso" in tables:
        op.drop_index("ix_eventos_uso_evento_dia", table_name="eventos_uso")
        op.drop_table("eventos_uso")

