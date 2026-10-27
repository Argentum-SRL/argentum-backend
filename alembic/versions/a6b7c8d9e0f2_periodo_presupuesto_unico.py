# -*- coding: utf-8 -*-
"""periodo_presupuesto_unico

Revision ID: a6b7c8d9e0f2
Revises: a5f6a7b8c9d0
Create Date: 2026-10-27

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6b7c8d9e0f2"
down_revision: Union[str, Sequence[str], None] = "a5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _migrar_datos(conn) -> None:
    """Respalda y elimina períodos duplicados y crea la restricción única."""
    conn.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS respaldo_periodos_a6b7 (
                id UUID PRIMARY KEY,
                presupuesto_id UUID NOT NULL,
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE NOT NULL,
                monto_limite NUMERIC(15, 2) NOT NULL,
                monto_usado NUMERIC(15, 2) NOT NULL,
                superado BOOLEAN NOT NULL,
                fecha_creacion TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
    )

    conn.execute(
        sa.text(
            """
            WITH duplicadas AS (
                SELECT
                    id,
                    presupuesto_id,
                    fecha_inicio,
                    fecha_fin,
                    monto_limite,
                    monto_usado,
                    superado,
                    fecha_creacion,
                    ROW_NUMBER() OVER (
                        PARTITION BY presupuesto_id, fecha_inicio
                        ORDER BY fecha_creacion ASC, id ASC
                    ) AS rn
                FROM periodos_presupuesto
            )
            INSERT INTO respaldo_periodos_a6b7 (
                id,
                presupuesto_id,
                fecha_inicio,
                fecha_fin,
                monto_limite,
                monto_usado,
                superado,
                fecha_creacion
            )
            SELECT
                id,
                presupuesto_id,
                fecha_inicio,
                fecha_fin,
                monto_limite,
                monto_usado,
                superado,
                fecha_creacion
            FROM duplicadas
            WHERE rn > 1
            ON CONFLICT (id) DO NOTHING
            """
        )
    )

    conn.execute(
        sa.text(
            """
            DELETE FROM periodos_presupuesto
            WHERE id IN (SELECT id FROM respaldo_periodos_a6b7)
            """
        )
    )

    conn.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'uq_periodos_presupuesto_presupuesto_inicio'
                ) THEN
                    ALTER TABLE periodos_presupuesto
                    ADD CONSTRAINT uq_periodos_presupuesto_presupuesto_inicio
                    UNIQUE (presupuesto_id, fecha_inicio);
                END IF;
            END $$;
            """
        )
    )


def _revertir_datos(conn) -> None:
    """Quita la restricción única, restaura las filas respaldadas y borra la tabla de respaldo."""
    conn.execute(
        sa.text(
            """
            ALTER TABLE periodos_presupuesto
            DROP CONSTRAINT IF EXISTS uq_periodos_presupuesto_presupuesto_inicio
            """
        )
    )

    existe_respaldo = conn.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = 'respaldo_periodos_a6b7'
            )
            """
        )
    ).scalar()

    if existe_respaldo:
        conn.execute(
            sa.text(
                """
                INSERT INTO periodos_presupuesto (
                    id,
                    presupuesto_id,
                    fecha_inicio,
                    fecha_fin,
                    monto_limite,
                    monto_usado,
                    superado,
                    fecha_creacion
                )
                SELECT
                    id,
                    presupuesto_id,
                    fecha_inicio,
                    fecha_fin,
                    monto_limite,
                    monto_usado,
                    superado,
                    fecha_creacion
                FROM respaldo_periodos_a6b7
                ON CONFLICT (id) DO NOTHING
                """
            )
        )

    conn.execute(sa.text("DROP TABLE IF EXISTS respaldo_periodos_a6b7"))


def upgrade() -> None:
    conn = op.get_bind()
    _migrar_datos(conn)


def downgrade() -> None:
    conn = op.get_bind()
    _revertir_datos(conn)

