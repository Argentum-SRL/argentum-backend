"""
Módulo puro de resolución y validación de entorno de ejecución para Argentum.
Ubicación: app/core/entorno.py

NO debe importar app.core.config ni módulos que se conecten a la base de datos o red.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse


def es_servidor_produccion(entorno: dict[str, str] | None = None) -> bool:
    """
    Determina si la aplicación se ejecuta en el servidor de producción (Railway).
    Comprueba si existe RAILWAY_ENVIRONMENT_ID o RAILWAY_PROJECT_ID en el entorno.
    """
    env = os.environ if entorno is None else entorno
    return "RAILWAY_ENVIRONMENT_ID" in env or "RAILWAY_PROJECT_ID" in env


def es_base_local(url: str) -> bool:
    """
    Determina si una URL de base de datos apunta a una base de datos local.
    Base local = host localhost, 127.0.0.1 o ::1, o una URL sqlite.
    """
    if not url:
        return False
    lower_url = url.lower()
    if lower_url.startswith("sqlite"):
        return True
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return host in ("localhost", "127.0.0.1", "::1")
    except Exception:
        return False


def opciones_conexion(url: str, entorno: dict[str, str] | None = None) -> dict[str, str]:
    """
    Retorna los argumentos de conexión (connect_args) para SQLAlchemy según la URL y el entorno.
    Para PostgreSQL:
      - '-csearch_path=public' por defecto.
      - Con ARGENTUM_PRODUCCION_SOLO_LECTURA=1 suma '-cdefault_transaction_read_only=on'.
    Para SQLite:
      - {}
    """
    if not url:
        return {}
    lower_url = url.lower()
    if lower_url.startswith("sqlite"):
        return {}

    env = os.environ if entorno is None else entorno
    if lower_url.startswith(("postgres", "postgresql")):
        solo_lectura = env.get("ARGENTUM_PRODUCCION_SOLO_LECTURA") == "1"
        if solo_lectura:
            return {"options": "-csearch_path=public -cdefault_transaction_read_only=on"}
        return {"options": "-csearch_path=public"}

    return {}
