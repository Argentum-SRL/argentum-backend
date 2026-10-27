"""
Utilidad compartida para asegurar que el servidor PostgreSQL local esté levantado.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time

from scripts.local import rutas


def asegurar_servidor_levantado(
    prefijo: str,
    pg_ctl: Path | str | None = None,
    data_dir: Path | str | None = None,
    log_file: Path | str | None = None,
) -> None:
    """
    Verifica si el servidor PostgreSQL local está levantado con pg_ctl status.
    Si no está corriendo, lo inicia con pg_ctl -D <data> -l <log> -w -t 180 start
    y timeout=200 segundos en subprocess.run (Decisión 6.a).
    """
    pg_ctl_path = Path(pg_ctl) if pg_ctl is not None else (rutas.pgsql_dir() / "bin" / "pg_ctl.exe")
    data_dir_path = Path(data_dir) if data_dir is not None else rutas.data_dir()
    log_file_path = Path(log_file) if log_file is not None else rutas.log_pg()

    if not pg_ctl_path.exists():
        raise FileNotFoundError(f"No existe pg_ctl en {pg_ctl_path}")

    res = subprocess.run(
        [str(pg_ctl_path), "-D", str(data_dir_path), "status"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"[{prefijo}] Servidor PostgreSQL local apagado. Arrancando con pg_ctl...")
        try:
            start_res = subprocess.run(
                [
                    str(pg_ctl_path),
                    "-D",
                    str(data_dir_path),
                    "-l",
                    str(log_file_path),
                    "-w",
                    "-t",
                    "180",
                    "start",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=200,
            )
        except subprocess.TimeoutExpired:
            print(f"[{prefijo}] ERROR: el servidor local no arrancó en 200 segundos.")
            sys.exit(1)

        if start_res.returncode != 0:
            print(f"[{prefijo}] ERROR: pg_ctl start retornó {start_res.returncode}")
            sys.exit(1)
        time.sleep(1)

