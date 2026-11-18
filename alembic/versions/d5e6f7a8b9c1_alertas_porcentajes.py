# -*- coding: utf-8 -*-
"""alertas_porcentajes en presupuestos

Revision ID: d5e6f7a8b9c1
Revises: c2d3e4f5a6b7
Create Date: 2026-11-17

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c1"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agrega la columna nullable para completarla con los valores por defecto de cada usuario
    op.add_column(
        "presupuestos",
        sa.Column("alertas_porcentajes", sa.JSON(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE presupuestos AS p
            SET alertas_porcentajes = COALESCE(
                (
                    SELECT CASE
                        WHEN cn.presupuesto_umbral_1_activo
                        THEN json_build_array(
                            LEAST(95, GREATEST(50, COALESCE(cn.presupuesto_umbral_1, 80))),
                            100
                        )
                        ELSE '[100]'::json
                    END
                    FROM configuracion_notificaciones cn
                    WHERE cn.usuario_id = p.usuario_id
                ),
                '[80, 100]'::json
            )
            """
        )
    )
    op.alter_column(
        "presupuestos",
        "alertas_porcentajes",
        existing_type=sa.JSON(),
        nullable=False,
        server_default=sa.text("'[80, 100]'"),
    )


def downgrade() -> None:
    op.drop_column("presupuestos", "alertas_porcentajes")
