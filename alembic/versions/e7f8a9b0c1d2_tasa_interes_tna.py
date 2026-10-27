# -*- coding: utf-8 -*-
"""tasa_interes_tna

Revision ID: e7f8a9b0c1d2
Revises: a6b7c8d9e0f2
Create Date: 2026-10-27

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _migrar_datos(conn) -> None:
    """
    Pasa tasa_interes de tasa mensual a TNA multiplicando por 12.
    Guarda: si existe alguna fila con tasa_interes > 250, aborta sin modificar datos.
    """
    cant_altas = conn.execute(
        sa.text("SELECT COUNT(*) FROM grupos_cuotas WHERE tasa_interes IS NOT NULL AND tasa_interes > 250")
    ).scalar()
    if cant_altas and cant_altas > 0:
        raise RuntimeError("Hay grupos de cuotas con tasa_interes mayor a 250: revisar antes de pasar a TNA.")

    conn.execute(
        sa.text("UPDATE grupos_cuotas SET tasa_interes = tasa_interes * 12 WHERE tasa_interes IS NOT NULL")
    )


def _revertir_datos(conn) -> None:
    """
    Revierte tasa_interes de TNA a tasa mensual dividiendo por 12 y redondeando a 4 decimales.
    """
    conn.execute(
        sa.text("UPDATE grupos_cuotas SET tasa_interes = ROUND(tasa_interes / 12, 4) WHERE tasa_interes IS NOT NULL")
    )


def upgrade() -> None:
    conn = op.get_bind()
    _migrar_datos(conn)


def downgrade() -> None:
    conn = op.get_bind()
    _revertir_datos(conn)

