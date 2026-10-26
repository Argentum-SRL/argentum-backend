# -*- coding: utf-8 -*-
"""
scripts/seed_categorias.py — Seed idempotente de categorías y subcategorías de Argentum.
Decisión 1 y 7.
"""
from __future__ import annotations

import logging
import os
import sys

# Añadir el directorio raíz al path para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.core.database import SessionLocal
from app.models.categoria import Categoria, EstadoCategoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria

logger = logging.getLogger(__name__)


def _categoria_en_uso(db: Session, cat_id) -> bool:
    """Verifica si alguna de las 6 tablas referencia esta categoria_id."""
    consultas = [
        "SELECT 1 FROM transacciones WHERE categoria_id = :cid LIMIT 1",
        "SELECT 1 FROM suscripciones WHERE categoria_id = :cid LIMIT 1",
        "SELECT 1 FROM presupuestos_categorias WHERE categoria_id = :cid LIMIT 1",
        "SELECT 1 FROM memoria_comercios WHERE categoria_id = :cid LIMIT 1",
        "SELECT 1 FROM facturas WHERE categoria_id = :cid LIMIT 1",
        "SELECT 1 FROM categorias_excluidas WHERE categoria_id = :cid LIMIT 1",
    ]
    for q in consultas:
        if db.execute(text(q), {"cid": cat_id}).scalar():
            return True
    return False


def seed_categorias(db: Session) -> None:
    """
    Seed idempotente del árbol canónico de categorías y subcategorías.
    Usa app/core/catalogo_categorias.py como fuente de verdad.
    """
    # --------------------------------------------------------------------------
    # Guarda 1 (Decisión 7):
    # Si encuentra una categoría de egreso activa llamada Recreativo, Comunicación
    # o Equipamiento del hogar y no existe Ocio, no hace nada y deja en el log:
    # "Seed de categorías salteado: falta la migración a5f6a7b8c9d0."
    # --------------------------------------------------------------------------
    cat_ocio = db.query(Categoria).filter(
        Categoria.nombre == "Ocio",
        Categoria.tipo == "egreso",
        Categoria.estado == EstadoCategoria.ACTIVA,
    ).first()

    if not cat_ocio:
        viejas = db.query(Categoria).filter(
            Categoria.nombre.in_(["Recreativo", "Comunicación", "Equipamiento del hogar"]),
            Categoria.tipo == "egreso",
            Categoria.estado == EstadoCategoria.ACTIVA,
        ).first()
        if viejas:
            msg = "Seed de categorías salteado: falta la migración a5f6a7b8c9d0."
            logger.warning(msg)
            print(msg)
            return

    # --------------------------------------------------------------------------
    # 1. Procesar categorías y subcategorías del catálogo canónico
    # --------------------------------------------------------------------------
    nombres_canonicos_cats: set[tuple[str, str]] = set()

    for cat_data in ARBOL_CATEGORIAS:
        nombres_canonicos_cats.add((cat_data.nombre, cat_data.tipo))

        # 1.1 Buscar si ya existe la categoría por nombre y tipo
        existente = db.query(Categoria).filter(
            Categoria.nombre == cat_data.nombre,
            Categoria.tipo == cat_data.tipo,
        ).first()

        if not existente:
            categoria = Categoria(
                nombre=cat_data.nombre,
                tipo=cat_data.tipo,
                orden=cat_data.orden,
                icono=cat_data.icono,
                color=cat_data.color,
                estado=EstadoCategoria.ACTIVA,
            )
            db.add(categoria)
            db.flush()
            print(f"[+] Categoria creada: {categoria.nombre} ({categoria.tipo})")
        else:
            categoria = existente
            categoria.orden = cat_data.orden
            categoria.icono = cat_data.icono
            categoria.color = cat_data.color
            if categoria.estado != EstadoCategoria.ACTIVA:
                categoria.estado = EstadoCategoria.ACTIVA
                print(f"[+] Categoria reactivada: {categoria.nombre} ({categoria.tipo})")

        # 1.2 Seedear subcategorías
        nombres_subs_canonicas: list[str] = []
        for sub_data in cat_data.subcategorias:
            nombres_subs_canonicas.append(sub_data.nombre)
            sub_existente = db.query(Subcategoria).filter(
                Subcategoria.categoria_id == categoria.id,
                Subcategoria.nombre == sub_data.nombre,
            ).first()

            if not sub_existente:
                subcategoria = Subcategoria(
                    categoria_id=categoria.id,
                    nombre=sub_data.nombre,
                    orden=sub_data.orden,
                    es_sistema=sub_data.es_sistema,
                    estado=EstadoSubcategoria.ACTIVA,
                )
                db.add(subcategoria)
                print(f"  [+] Subcategoria creada: {sub_data.nombre} en {categoria.nombre} (orden {sub_data.orden})")
            else:
                sub_existente.orden = sub_data.orden
                sub_existente.es_sistema = sub_data.es_sistema
                if sub_existente.estado != EstadoSubcategoria.ACTIVA:
                    sub_existente.estado = EstadoSubcategoria.ACTIVA
                    print(f"  [+] Subcategoria reactivada: {sub_existente.nombre} en {categoria.nombre}")

        # 1.3 Archivar subcategorías que sobran en esta categoría
        sub_sobrantes = db.query(Subcategoria).filter(
            Subcategoria.categoria_id == categoria.id,
            Subcategoria.estado == EstadoSubcategoria.ACTIVA,
            ~Subcategoria.nombre.in_(nombres_subs_canonicas),
        ).all()
        for sub_del in sub_sobrantes:
            sub_del.estado = EstadoSubcategoria.ARCHIVADA
            print(f"  [-] Subcategoria archivada: {sub_del.nombre} en {categoria.nombre}")

    # --------------------------------------------------------------------------
    # Guarda 2 (Decisión 7):
    # Una categoría activa que no está en el catálogo se archiva solo si ninguna
    # fila de las 6 tablas la usa; si se usa, queda y va un error al log con su nombre.
    # --------------------------------------------------------------------------
    cats_activas_db = db.query(Categoria).filter(
        Categoria.estado == EstadoCategoria.ACTIVA
    ).all()

    for c_act in cats_activas_db:
        if (c_act.nombre, c_act.tipo.value if hasattr(c_act.tipo, "value") else str(c_act.tipo)) not in nombres_canonicos_cats:
            if _categoria_en_uso(db, c_act.id):
                error_msg = f"Categoría activa fuera del catálogo en uso no puede archivarse: {c_act.nombre}"
                logger.error(error_msg)
                print(f"ERROR: {error_msg}")
            else:
                c_act.estado = EstadoCategoria.ARCHIVADA
                print(f"[-] Categoria archivada: {c_act.nombre} ({c_act.tipo})")

    db.commit()
    try:
        from app.services.categoria_service import invalidar_cache_categorias_globales
        invalidar_cache_categorias_globales()
    except Exception:
        pass


if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed_categorias(db)
    finally:
        db.close()
