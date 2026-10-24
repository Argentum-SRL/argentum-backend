"""
Wrapper para ejecutar cualquier comando contra la base de datos PostgreSQL local.
Arranca el servidor si no está levantado (pg_ctl status), configura las variables de entorno
apuntando a localhost:5433/argentum_local y ejecuta el comando solicitado.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.local import rutas


def asegurar_servidor_levantado():
    pg_ctl = rutas.pgsql_dir() / "bin" / "pg_ctl.exe"
    data_dir = rutas.data_dir()
    log_file = rutas.log_pg()

    if not pg_ctl.exists():
        raise FileNotFoundError(f"No existe pg_ctl en {pg_ctl}")

    res = subprocess.run(
        [str(pg_ctl), "-D", str(data_dir), "status"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print("[con_base_local] Servidor PostgreSQL local apagado. Arrancando con pg_ctl...")
        start_res = subprocess.run(
            [str(pg_ctl), "-D", str(data_dir), "-l", str(log_file), "-w", "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if start_res.returncode != 0:
            print(f"[con_base_local] ERROR: pg_ctl start retorno {start_res.returncode}")
            sys.exit(1)
        time.sleep(1)


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

    asegurar_servidor_levantado()
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
