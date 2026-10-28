# -*- coding: utf-8 -*-
"""bloque_f_espejos

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-10-27

"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLAS_CON_CATEGORIA = (
    "transacciones",
    "suscripciones",
    "presupuestos_categorias",
    "memoria_comercios",
    "facturas",
    "categorias_excluidas",
)

SUBCATEGORIAS_NUEVAS = (
    ("Otros", "egreso", "Pasó por mi cuenta", 4, False, "fuera"),
    ("Otros", "ingreso", "Pasó por mi cuenta", 3, False, None),
)


def _migrar_datos(conn) -> None:
    """
    Inserta las subcategorías «Pasó por mi cuenta» en Otros de egreso y de ingreso.
    Si la categoría Otros de ese tipo no existe (activa), lanza RuntimeError sin cambiar nada.
    Si la subcategoría ya existe, no hace nada.
    """
    # Guarda para la CI (base sin datos): si no hay ninguna categoría, no hay nada que agregar
    total_categorias = conn.execute(sa.text("SELECT count(*) FROM categorias")).scalar()
    if not total_categorias:
        return

    cat_ids = {}
    for cat_nombre, cat_tipo, _sub, _orden, _es_sis, _ipc in SUBCATEGORIAS_NUEVAS:
        cat_row = conn.execute(
            sa.text(
                "SELECT id FROM categorias WHERE nombre = :nom AND tipo = :tipo AND estado = 'activa'"
            ),
            {"nom": cat_nombre, "tipo": cat_tipo},
        ).first()

        if not cat_row:
            raise RuntimeError(f"Falta la categoría Otros de {cat_tipo}.")

        cat_ids[(cat_nombre, cat_tipo)] = cat_row[0]

    for cat_nombre, cat_tipo, sub_nombre, orden, es_sistema, _ipc in SUBCATEGORIAS_NUEVAS:
        cat_id = cat_ids[(cat_nombre, cat_tipo)]

        sub_row = conn.execute(
            sa.text(
                "SELECT id FROM subcategorias WHERE categoria_id = :cid AND nombre = :snom"
            ),
            {"cid": cat_id, "snom": sub_nombre},
        ).first()

        if not sub_row:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO subcategorias (id, categoria_id, nombre, orden, es_sistema, estado)
                    VALUES (:id, :cid, :nombre, :orden, :es_sistema, 'activa')
                    """
                ),
                {
                    "id": str(uuid4()),
                    "cid": cat_id,
                    "nombre": sub_nombre,
                    "orden": orden,
                    "es_sistema": es_sistema,
                },
            )


def _revertir_datos(conn) -> None:
    """
    Revierte los datos: si alguna fila de las 6 tablas con categoría usa
    «Pasó por mi cuenta», lanza RuntimeError. Si no, las borra.
    """
    subcat_ids = []
    for cat_nombre, cat_tipo, sub_nombre, _orden, _es_sis, _ipc in SUBCATEGORIAS_NUEVAS:
        row = conn.execute(
            sa.text(
                """
                SELECT s.id FROM subcategorias s
                JOIN categorias c ON s.categoria_id = c.id
                WHERE c.nombre = :cnom AND c.tipo = :ctipo AND s.nombre = :snom
                """
            ),
            {"cnom": cat_nombre, "ctipo": cat_tipo, "snom": sub_nombre},
        ).first()
        if row:
            subcat_ids.append(row[0])

    for sid in subcat_ids:
        for tabla in TABLAS_CON_CATEGORIA:
            cant = conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {tabla} WHERE subcategoria_id = :sid"),
                {"sid": sid},
            ).scalar()
            if cant and cant > 0:
                raise RuntimeError("No se puede bajar: «Pasó por mi cuenta» tiene movimientos.")

    for sid in subcat_ids:
        conn.execute(
            sa.text("DELETE FROM subcategorias WHERE id = :sid"),
            {"sid": sid},
        )


def upgrade() -> None:
    conn = op.get_bind()
    op.add_column("transacciones", sa.Column("tipo_espejo", sa.String(length=20), nullable=True))
    conn.execute(sa.text("UPDATE transacciones SET tipo_espejo = 'reintegro' WHERE reintegro_de_id IS NOT NULL"))
    op.create_check_constraint(
        "ck_transacciones_tipo_espejo",
        "transacciones",
        "(reintegro_de_id IS NULL AND tipo_espejo IS NULL) OR (reintegro_de_id IS NOT NULL AND tipo_espejo IN ('reintegro', 'parte'))",
    )
    _migrar_datos(conn)


def downgrade() -> None:
    conn = op.get_bind()
    _revertir_datos(conn)
    op.drop_constraint("ck_transacciones_tipo_espejo", "transacciones", type_="check")
    op.drop_column("transacciones", "tipo_espejo")
