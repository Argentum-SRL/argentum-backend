"""
Módulo para resolución segura de la URL de base de datos de producción.
Ubicación: scripts/local/produccion.py

NO debe importar nada de app salvo app/core/entorno.py.
Nunca imprime la URL ni la contraseña; para mostrar la base usa 'PRODUCCIÓN (<host>)'.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.entorno import es_base_local
from scripts.local import rutas


def produccion_env_path() -> Path:
    """Retorna la ruta al archivo produccion.env dentro de secretos_dir()."""
    return rutas.secretos_dir() / "produccion.env"


def backend_env_path() -> Path:
    """Retorna la ruta al archivo .env del backend."""
    return BACKEND_DIR / ".env"


def leer_produccion_env(env_path: Path | None = None) -> str | None:
    """Lee y retorna PRODUCCION_DATABASE_URL desde produccion.env si existe."""
    p = env_path or produccion_env_path()
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("PRODUCCION_DATABASE_URL="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                return val
    return None


def leer_backend_env_database_url(env_path: Path | None = None) -> str | None:
    """Lee y retorna DATABASE_URL desde el .env del backend si existe."""
    p = env_path or backend_env_path()
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                return val
    return None


def descripcion_base(url: str) -> str:
    """Retorna 'PRODUCCIÓN (<host>)' sin exponer contraseñas ni cadenas sensibles."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "desconocido"
    except Exception:
        host = "desconocido"
    return f"PRODUCCIÓN ({host})"


def url_para_refresco() -> str:
    """
    Retorna la URL para refresco de base local:
    - produccion.env si existe.
    - Si no existe y DATABASE_URL del .env no es local, retorna ese valor con aviso.
    - Si no, lanza error.
    """
    prod_env_file = produccion_env_path()
    url = leer_produccion_env(prod_env_file)
    if url is not None:
        if es_base_local(url):
            raise ValueError("La URL de produccion.env apunta a una base local.")
        return url

    # Si no existe produccion.env
    b_url = leer_backend_env_database_url()
    if b_url is not None and not es_base_local(b_url):
        print("AVISO: la URL de producción todavía está en el .env (falta correr separar_produccion.py).")
        return b_url

    raise ValueError("No hay URL de producción para el refresco.")


def url_para_comando() -> str:
    """
    Retorna la URL para ejecución con con_produccion.py:
    - Solo produccion.env (nunca el .env).
    - Si falta, lanza FileNotFoundError.
    - Si apunta a una base local, lanza ValueError.
    """
    prod_env_file = produccion_env_path()
    if not prod_env_file.exists():
        raise FileNotFoundError(
            f"Falta {prod_env_file} (se crea con scripts\\local\\separar_produccion.py)."
        )

    url = leer_produccion_env(prod_env_file)
    if not url:
        raise FileNotFoundError(
            f"Falta {prod_env_file} (se crea con scripts\\local\\separar_produccion.py)."
        )

    if es_base_local(url):
        raise ValueError("La URL de produccion.env apunta a una base local.")

    return url
