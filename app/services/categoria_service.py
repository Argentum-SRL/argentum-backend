"""
app/services/categoria_service.py — Servicio de categorías y subcategorías con cache en memoria (TTLCache).
"""
import time
import logging
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.categoria import Categoria, EstadoCategoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria

logger = logging.getLogger(__name__)

try:
    from cachetools import TTLCache  # type: ignore[import-untyped,import-not-found]
    _cache_categorias_globales = TTLCache(maxsize=10, ttl=900)
except ImportError:
    class _SimpleTTLCache(dict):
        """Fallback de cache TTL en memoria cuando cachetools no está disponible en el entorno de análisis."""
        def __init__(self, maxsize: int = 10, ttl: int = 900):
            super().__init__()
            self._ttl = ttl
            self._expires: dict[str, float] = {}

        def __getitem__(self, key: Any) -> Any:
            if key in self._expires and time.time() > self._expires[key]:
                self.pop(key, None)
                self._expires.pop(key, None)
                raise KeyError(key)
            return super().__getitem__(key)

        def get(self, key: Any, default: Any = None) -> Any:
            try:
                return self[key]
            except KeyError:
                return default

        def __setitem__(self, key: Any, value: Any) -> None:
            self._expires[key] = time.time() + self._ttl
            super().__setitem__(key, value)

        def clear(self) -> None:
            self._expires.clear()
            super().clear()

    _cache_categorias_globales = _SimpleTTLCache(maxsize=10, ttl=900)

_CACHE_KEY_GLOBALES = "categorias_y_subcategorias_globales"



def invalidar_cache_categorias_globales() -> None:
    """Invalida manualmente el cache de categorías y subcategorías globales."""
    _cache_categorias_globales.clear()
    logger.info("Cache de categorías globales invalidado.")


def obtener_categorias_globales(db: Session) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Devuelve las categorías y subcategorías globales activas.
    Utiliza un cache en memoria del proceso con TTL de 15 minutos.
    Retorna una tupla (categorias_globales, subcategorias_globales) como listas de dicts.
    """
    cached = _cache_categorias_globales.get(_CACHE_KEY_GLOBALES)
    if cached is not None:
        return cached

    # 1. Consultar categorías globales activas
    categorias_stmt = select(Categoria).where(
        Categoria.es_global == True,
        Categoria.estado == EstadoCategoria.ACTIVA,
    ).order_by(Categoria.nombre)
    categorias = db.execute(categorias_stmt).scalars().all()

    # 2. Consultar subcategorías globales activas
    subcategorias_stmt = select(Subcategoria).where(
        Subcategoria.es_global == True,
        Subcategoria.estado == EstadoSubcategoria.ACTIVA,
    ).order_by(Subcategoria.orden, Subcategoria.nombre)
    subcategorias = db.execute(subcategorias_stmt).scalars().all()

    cats_data = [
        {
            "id": c.id,
            "nombre": c.nombre,
            "tipo": c.tipo,
            "icono": c.icono,
            "color": c.color,
            "es_global": c.es_global,
            "creador_id": c.creador_id,
            "estado": c.estado,
        }
        for c in categorias
    ]

    subs_data = [
        {
            "id": s.id,
            "categoria_id": s.categoria_id,
            "nombre": s.nombre,
            "orden": s.orden,
            "es_global": s.es_global,
            "creador_id": s.creador_id,
            "estado": s.estado,
        }
        for s in subcategorias
    ]

    resultado = (cats_data, subs_data)
    _cache_categorias_globales[_CACHE_KEY_GLOBALES] = resultado
    logger.debug(
        "Categorías y subcategorías globales cargadas en cache (total cats: %d, subs: %d)",
        len(cats_data),
        len(subs_data),
    )
    return resultado
