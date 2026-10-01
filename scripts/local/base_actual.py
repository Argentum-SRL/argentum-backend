"""
Detección e impresión de la base de datos actualmente en uso según DATABASE_URL.
No imprime contraseñas ni credenciales.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

def obtener_mensaje_base() -> str:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        env_file = Path(__file__).resolve().parents[2] / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    db_url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not db_url:
        return "BASE: DESCONOCIDA (DATABASE_URL no configurada)"

    try:
        parsed = urlparse(db_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        db_name = parsed.path.lstrip("/") or "argentum_local"

        if host in ("localhost", "127.0.0.1") and port == 5433:
            return f"BASE: LOCAL ({host}:{port}/{db_name})"
        else:
            return f"BASE: PRODUCCION ({host})"
    except Exception as e:
        return f"BASE: ERROR al parsear DATABASE_URL ({e})"

def imprimir_base_actual():
    print(obtener_mensaje_base())

if __name__ == "__main__":
    imprimir_base_actual()
