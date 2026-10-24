"""
scripts/ci/cargar_esquema.py

Carga el esquema de producción en la revisión a3d4e5f6a7b8 sin datos
contra la base de datos local o de integración continua (CI).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
import psycopg2
from sqlalchemy.engine import make_url


def url_permitida(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = make_url(url)
        return (
            parsed.host in ("localhost", "127.0.0.1")
            and parsed.database in ("argentum_ci", "argentum_ci_prueba")
        )
    except Exception:
        return False


def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    if not url_permitida(db_url):
        print("Solo contra una base local o de la CI.", file=sys.stderr)
        sys.exit(2)

    sql_path = Path(__file__).resolve().parent / "esquema_a3d4e5f6a7b8.sql"
    if not sql_path.exists():
        print(f"No existe el archivo de esquema: {sql_path}", file=sys.stderr)
        sys.exit(1)

    sql_content = sql_path.read_text(encoding="utf-8")

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE;")
        cur.execute(sql_content)
    conn.close()
    print("Esquema cargado exitosamente.")


if __name__ == "__main__":
    main()
