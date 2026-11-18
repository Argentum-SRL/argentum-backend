from typing import Any, List
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.catalogo_categorias import resolver_alias
from app.core.constants import CATEGORIAS_SISTEMA
from app.core.database import get_db
from app.models.categoria import TipoCategoria
from app.models.usuario import Usuario
from app.schemas.categoria import CategoriaBusquedaResponse, CategoriaRead
from app.schemas.subcategoria import SubcategoriaRead
from app.services import (
    categoria_busqueda_service,
    categoria_service,
    clasificador_concepto_service,
    memoria_comercio_service,
)

router = APIRouter(prefix="/categorias", tags=["categorias"])


def _tipo_a_str(valor: Any) -> str:
    """Convierte el tipo de categoría (enum o texto) a minúsculas."""
    if isinstance(valor, TipoCategoria):
        return valor.value
    return str(valor).lower()


def _construir_mapas_categorias(
    db: Session,
    tipo: str,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
]:
    """Indexa categorías y subcategorías activas visibles del tipo pedido."""
    cats_globales, subs_globales = categoria_service.obtener_categorias_globales(db)
    cats_por_id: dict[str, dict[str, Any]] = {}
    cats_por_nombre: dict[str, dict[str, Any]] = {}
    for cat in cats_globales:
        if _tipo_a_str(cat["tipo"]) != tipo:
            continue
        nombre_canon = resolver_alias(cat["nombre"])
        if nombre_canon in CATEGORIAS_SISTEMA or cat["nombre"] in CATEGORIAS_SISTEMA:
            continue
        cats_por_id[str(cat["id"])] = cat
        cats_por_nombre[nombre_canon] = cat
        cats_por_nombre[cat["nombre"]] = cat

    subs_por_id: dict[str, dict[str, Any]] = {}
    subs_por_cat_nombre: dict[tuple[str, str], dict[str, Any]] = {}
    for sub in subs_globales:
        cat_id_str = str(sub["categoria_id"])
        if cat_id_str not in cats_por_id or sub.get("es_sistema"):
            continue
        subs_por_id[str(sub["id"])] = sub
        sub_canon = resolver_alias(sub["nombre"])
        subs_por_cat_nombre[(cat_id_str, sub_canon)] = sub
        subs_por_cat_nombre[(cat_id_str, sub["nombre"])] = sub

    return cats_por_id, cats_por_nombre, subs_por_id, subs_por_cat_nombre


def _resolver_par_bd(
    cat_nombre: str,
    sub_nombre: str | None,
    cats_por_nombre: dict[str, dict[str, Any]],
    subs_por_cat_nombre: dict[tuple[str, str], dict[str, Any]],
) -> tuple[dict[str, Any], UUID | None, str | None] | None:
    """Resuelve nombres del catálogo a filas activas de la base o None si faltan."""
    cat = cats_por_nombre.get(resolver_alias(cat_nombre)) or cats_por_nombre.get(cat_nombre)
    if not cat:
        return None
    if sub_nombre is None:
        return cat, None, None
    cat_id_str = str(cat["id"])
    sub = (
        subs_por_cat_nombre.get((cat_id_str, resolver_alias(sub_nombre)))
        or subs_por_cat_nombre.get((cat_id_str, sub_nombre))
    )
    if not sub:
        return None
    return cat, sub["id"], sub["nombre"]


def _agregar_pares(
    pares: list[tuple[str, str | None]],
    origen: str,
    resultados: list[dict[str, Any]],
    vistos: set[tuple[UUID, UUID | None]],
    cats_por_nombre: dict[str, dict[str, Any]],
    subs_por_cat_nombre: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Resuelve cada par contra la base y lo suma a resultados evitando duplicados."""
    for cat_nombre, sub_nombre in pares:
        resuelto = _resolver_par_bd(
            cat_nombre, sub_nombre, cats_por_nombre, subs_por_cat_nombre
        )
        if not resuelto:
            continue
        cat, sub_id, sub_out = resuelto
        par_id = (cat["id"], sub_id)
        if par_id in vistos:
            continue
        vistos.add(par_id)
        resultados.append(
            {
                "categoria_id": cat["id"],
                "subcategoria_id": sub_id,
                "categoria": cat["nombre"],
                "subcategoria": sub_out,
                "origen": origen,
            }
        )


@router.get("", response_model=List[CategoriaRead])
def list_categorias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las categorías globales (desde cache).
    """
    cats_globales, _ = categoria_service.obtener_categorias_globales(db)
    return cats_globales


@router.get("/buscar", response_model=CategoriaBusquedaResponse)
def buscar_categorias(
    q: str = Query(default=""),
    tipo: str = Query(default="egreso"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Busca categorías en memoria del usuario y catálogo local, sin llamar a la IA.
    """
    if tipo not in ("egreso", "ingreso"):
        return {"resultados": []}

    q_norm = categoria_busqueda_service.normalizar_busqueda(q)
    if len(q_norm) < 2:
        return {"resultados": []}

    cats_por_id, cats_por_nombre, subs_por_id, subs_por_cat_nombre = (
        _construir_mapas_categorias(db, tipo)
    )

    resultados = categoria_busqueda_service.buscar_en_memoria(
        db, current_user.id, q, tipo, cats_por_id, subs_por_id
    )
    vistos: set[tuple[UUID, UUID | None]] = {
        (item["categoria_id"], item["subcategoria_id"]) for item in resultados
    }

    pares_catalogo = categoria_busqueda_service.buscar_en_catalogo(q, tipo)
    _agregar_pares(
        pares_catalogo,
        "catalogo",
        resultados,
        vistos,
        cats_por_nombre,
        subs_por_cat_nombre,
    )

    return {"resultados": resultados}


@router.get("/sugerir", response_model=CategoriaBusquedaResponse)
def sugerir_categorias(
    q: str = Query(default=""),
    tipo: str = Query(default="egreso"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Sugiere categorías por memoria exacta del comercio o por clasificador IA.
    """
    if tipo not in ("egreso", "ingreso"):
        return {"resultados": []}

    q_norm = categoria_busqueda_service.normalizar_busqueda(q)
    if len(q_norm) < 3:
        return {"resultados": []}

    cats_por_id, cats_por_nombre, subs_por_id, subs_por_cat_nombre = (
        _construir_mapas_categorias(db, tipo)
    )

    mem = memoria_comercio_service.buscar(db, current_user.id, q, tipo)
    if mem is not None:
        cat = cats_por_id.get(str(mem.categoria_id))
        if cat:
            if mem.subcategoria_id is None:
                return {
                    "resultados": [
                        {
                            "categoria_id": cat["id"],
                            "subcategoria_id": None,
                            "categoria": cat["nombre"],
                            "subcategoria": None,
                            "origen": "memoria",
                        }
                    ]
                }
            sub = subs_por_id.get(str(mem.subcategoria_id))
            if sub and str(sub["categoria_id"]) == str(cat["id"]):
                return {
                    "resultados": [
                        {
                            "categoria_id": cat["id"],
                            "subcategoria_id": sub["id"],
                            "categoria": cat["nombre"],
                            "subcategoria": sub["nombre"],
                            "origen": "memoria",
                        }
                    ]
                }

    pares_ia, _ = clasificador_concepto_service.clasificar_concepto(
        db, current_user, q, tipo
    )
    resultados: list[dict[str, Any]] = []
    vistos: set[tuple[UUID, UUID | None]] = set()
    _agregar_pares(
        pares_ia,
        "ia",
        resultados,
        vistos,
        cats_por_nombre,
        subs_por_cat_nombre,
    )
    return {"resultados": resultados}

@router.get("/{categoria_id}/subcategorias", response_model=List[SubcategoriaRead])
def list_subcategorias(
    categoria_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las subcategorías de una categoría específica (globales desde cache).
    """
    _, subs_globales = categoria_service.obtener_categorias_globales(db)
    return [s for s in subs_globales if str(s["categoria_id"]) == str(categoria_id)]

@router.get("/subcategorias", response_model=List[SubcategoriaRead])
def list_all_subcategorias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista todas las subcategorías activas (globales desde cache).
    """
    _, subs_globales = categoria_service.obtener_categorias_globales(db)
    return subs_globales


