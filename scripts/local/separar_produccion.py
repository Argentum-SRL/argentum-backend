"""
Herramienta separar_produccion.py:
Separa la URL de base de datos de producción del .env principal hacia produccion.env,
reemplazando DATABASE_URL en el .env para que apunte de forma segura a la copia local.
Ubicación: scripts/local/separar_produccion.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.entorno import es_base_local
from scripts.local import rutas


def leer_pgpassword(env_path: Path) -> str:
    if not env_path.exists():
        raise FileNotFoundError(f"No existe {env_path}")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("PGPASSWORD="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                return val
    raise ValueError(f"No se encontró PGPASSWORD en {env_path}")


def main():
    backend_env = BACKEND_DIR / ".env"
    prod_env = rutas.secretos_dir() / "produccion.env"

    # Soporte para --revertir <respaldo>
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == "--revertir":
        respaldo_path = Path(args[1])
        if not respaldo_path.exists():
            print(f"ERROR: No existe el archivo de respaldo {respaldo_path}")
            sys.exit(2)

        # Restaurar .env desde respaldo
        backend_env.write_bytes(respaldo_path.read_bytes())

        # Renombrar produccion.env si existe
        if prod_env.exists():
            ahora = datetime.now().strftime("%Y%m%d_%H%M%S")
            revertido_path = prod_env.parent / f"produccion.env.revertido_{ahora}"
            os.rename(str(prod_env), str(revertido_path))

        print(f"OK: .env restaurado desde {respaldo_path}.")
        sys.exit(0)

    if not backend_env.exists():
        print(f"ERROR: No existe el archivo {backend_env}")
        sys.exit(2)

    # Analizar líneas del .env preservando terminaciones de línea
    raw_bytes = backend_env.read_bytes()
    raw_text = raw_bytes.decode("utf-8")
    lines_with_endings = raw_text.splitlines(keepends=True)

    db_url_indices = []
    old_db_url = ""
    for idx, l in enumerate(lines_with_endings):
        stripped = l.strip()
        if stripped.startswith("DATABASE_URL="):
            db_url_indices.append(idx)
            old_db_url = stripped.split("=", 1)[1].strip().strip('"').strip("'")

    if len(db_url_indices) != 1:
        print(f"ERROR: El .env debe tener exactamente una línea DATABASE_URL= (se encontraron {len(db_url_indices)}).")
        sys.exit(2)

    if es_base_local(old_db_url):
        print("El .env ya apunta a la copia local: no hay nada que separar.")
        sys.exit(2)

    if prod_env.exists():
        print("Ya existe produccion.env: no se toca.")
        sys.exit(2)

    pg_local_env = rutas.pg_local_env()
    try:
        pg_password = leer_pgpassword(pg_local_env)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(2)

    # 1. Copia byte a byte del .env a env_respaldo_<aaaammdd_hhmmss>.env
    secretos_dir = rutas.secretos_dir()
    secretos_dir.mkdir(parents=True, exist_ok=True)
    ahora = datetime.now().strftime("%Y%m%d_%H%M%S")
    respaldo_file = secretos_dir / f"env_respaldo_{ahora}.env"
    respaldo_file.write_bytes(raw_bytes)

    # 2. Escribir produccion.env
    host = urlparse(old_db_url).hostname or "desconocido"
    prod_env_content = (
        "# Base de datos de producción de Argentum (separada de .env)\n"
        f"PRODUCCION_DATABASE_URL={old_db_url}\n"
    )
    prod_env.write_text(prod_env_content, encoding="utf-8")

    # 3. Reescribir solo la línea DATABASE_URL del .env
    target_idx = db_url_indices[0]
    target_line = lines_with_endings[target_idx]
    newline = "\r\n" if target_line.endswith("\r\n") else "\n"
    new_db_line = f"DATABASE_URL=postgresql://postgres:{pg_password}@localhost:5433/argentum_local{newline}"

    new_lines = list(lines_with_endings)
    new_lines[target_idx] = new_db_line
    new_env_content = "".join(new_lines)
    backend_env.write_bytes(new_env_content.encode("utf-8"))

    # 4. Releer y comprobar ambos archivos
    check_prod_lines = prod_env.read_text(encoding="utf-8").splitlines()
    check_prod_url = None
    for pl in check_prod_lines:
        if pl.startswith("PRODUCCION_DATABASE_URL="):
            check_prod_url = pl.split("=", 1)[1].strip()
            break
    if check_prod_url != old_db_url:
        print("ERROR: Verificación fallida en produccion.env")
        sys.exit(2)

    check_backend_lines = backend_env.read_text(encoding="utf-8").splitlines()
    check_local_url = None
    for bl in check_backend_lines:
        if bl.startswith("DATABASE_URL="):
            check_local_url = bl.split("=", 1)[1].strip()
            break
    expected_local = f"postgresql://postgres:{pg_password}@localhost:5433/argentum_local"
    if check_local_url != expected_local:
        print("ERROR: Verificación fallida en .env reescrito")
        sys.exit(2)

    # 5. Salida estándar
    print(f"OK: la URL de producción quedó en {prod_env} (PRODUCCIÓN {host}).")
    print("OK: el .env ahora apunta a la copia local (localhost:5433/argentum_local).")


if __name__ == "__main__":
    main()
