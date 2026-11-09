# -*- coding: utf-8 -*-
"""expectativas_inflacion

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-11-04

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "expectativas_inflacion",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("relevamiento", sa.String(length=7), nullable=False),
        sa.Column("periodo", sa.String(length=7), nullable=False),
        sa.Column("valor", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("fuente", sa.String(length=20), server_default="REM BCRA", nullable=False),
        sa.Column("fecha_actualizacion", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("relevamiento", "periodo", name="uq_expectativas_inflacion_relevamiento_periodo"),
    )


def downgrade() -> None:
    op.drop_table("expectativas_inflacion")

