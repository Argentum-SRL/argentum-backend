# -*- coding: utf-8 -*-
"""pregunta_saldo

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-10-29

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b0c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("billeteras")]

    if "pregunta_saldo_pospuesta_hasta" not in columns:
        op.add_column("billeteras", sa.Column("pregunta_saldo_pospuesta_hasta", sa.Date(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns("billeteras")]

    if "pregunta_saldo_pospuesta_hasta" in columns:
        op.drop_column("billeteras", "pregunta_saldo_pospuesta_hasta")

