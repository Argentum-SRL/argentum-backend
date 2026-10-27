"""
Wrapper para ejecutar cualquier comando contra la base de datos PostgreSQL local.
Arranca el servidor si no está levantado (pg_ctl status), configura las variables de entorno
apuntando a localhost:5433/argentum_local y ejecuta el comando solicitado.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.local import rutas
from scripts.local.servidor_local import asegurar_servidor_levantado


def obtener_credenciales_locales():
    env_file = rutas.pg_local_env()
    if not env_file.exists():
        raise FileNotFoundError(f"No existe {env_file}")
    env_vars = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars


def main():
    if len(sys.argv) < 2:
        print("Uso: python con_base_local.py <comando...>")
        sys.exit(1)

    asegurar_servidor_levantado("con_base_local")
    local_env_vars = obtener_credenciales_locales()

    env = os.environ.copy()
    for k, v in local_env_vars.items():
        env[k] = v

    password = local_env_vars.get("PGPASSWORD", "")
    env["DATABASE_URL"] = f"postgresql://postgres:{password}@localhost:5433/argentum_local"
    env["PGHOST"] = "localhost"
    env["PGPORT"] = "5433"
    env["PGUSER"] = "postgres"
    env["PGPASSWORD"] = password
    env["PGDATABASE"] = "argentum_local"

    cmd = sys.argv[1:]
    proc = subprocess.run(cmd, env=env)
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
