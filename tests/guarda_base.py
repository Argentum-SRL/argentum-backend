"""
tests/guarda_base.py — Módulo de ayuda de los tests para validar URLs de bases de datos.
"""
from __future__ import annotations

from typing import Mapping
from urllib.parse import urlparse

from app.core.entorno import es_servidor_produccion

MENSAJE_BLOQUEO = "pytest solo corre contra la copia local o la base de la CI."


def base_permitida_para_pruebas(url: str, entorno: Mapping[str, str]) -> bool:
    """
    Determina si la base de datos configurada está permitida para correr pruebas.
    Devuelve False si entorno tiene ARGENTUM_PRODUCCION_CONFIRMADA == "1" o si es_servidor_produccion(entorno).
    Si no:
      True si la url empieza con "sqlite";
      True si, parseada con urlparse, el host es "localhost" o "127.0.0.1", el puerto es 5433 y la base (path sin "/") es "argentum_local";
      True si el host es "localhost" o "127.0.0.1", el puerto es 5432, la base es "argentum_ci" y entorno tiene ARGENTUM_PG_TESTS_CI == "1";
      False en cualquier otro caso (también si la url está vacía o no se puede parsear).
    """
    if entorno.get("ARGENTUM_PRODUCCION_CONFIRMADA") == "1" or es_servidor_produccion(dict(entorno)):
        return False
    if not url:
        return False
    if url.lower().startswith("sqlite"):
        return True
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
        path = (parsed.path or "").lstrip("/")
        if host in ("localhost", "127.0.0.1") and port == 5433 and path == "argentum_local":
            return True
        if host in ("localhost", "127.0.0.1") and port == 5432 and path == "argentum_ci" and entorno.get("ARGENTUM_PG_TESTS_CI") == "1":
            return True
        return False
    except Exception:
        return False

