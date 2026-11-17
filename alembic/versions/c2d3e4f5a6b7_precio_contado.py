# -*- coding: utf-8 -*-
"""precio_contado

Revision ID: c2d3e4f5a6b7
Revises: b6c7d8e9f0a1
Create Date: 2026-11-17

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b6c7d8e9f0a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "grupos_cuotas",
        sa.Column(
            "precio_contado",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("grupos_cuotas", "precio_contado")
