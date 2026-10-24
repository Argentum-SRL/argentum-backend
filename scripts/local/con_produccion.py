"""
Script wrapper con_produccion.py: único camino para ejecutar comandos contra la base de producción.
Ubicación: scripts/local/con_produccion.py

Uso:
  python scripts/local/con_produccion.py [--solo-lectura] <comando...>
"""
from __future__ import annotations

import os
import sys
import time
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.local import rutas
from scripts.local.produccion import url_para_comando


def main():
    args = sys.argv[1:]
    solo_lectura = False
    if args and args[0] == "--solo-lectura":
        solo_lectura = True
        args = args[1:]

    if not args:
        print("Uso: python con_produccion.py [--solo-lectura] <comando...>")
        sys.exit(2)

    # 1. Obtener URL de producción
    try:
        prod_url = url_para_comando()
    except Exception as e:
        print(str(e))
        sys.exit(2)

    # 2. Control de terminal interactiva
    if not sys.stdin.isatty():
        print("Este comando se corre solo a mano desde PowerShell.")
        sys.exit(2)

    # 3. Confirmación explícita
    parsed = urlparse(prod_url)
    host = parsed.hostname or "desconocido"
    modo = "LECTURA" if solo_lectura else "ESCRITURA"
    palabra_esperada = "LECTURA" if solo_lectura else "PRODUCCION"
    comando_str = " ".join(args)

    print(f"Vas a correr contra PRODUCCIÓN ({host}) en modo {modo}: {comando_str}")
    try:
        entrada = input(f"Escribí {palabra_esperada} para confirmar: ")
    except (EOFError, KeyboardInterrupt):
        print("Cancelado: no se corrió nada.")
        sys.exit(2)

    if entrada != palabra_esperada:
        print("Cancelado: no se corrió nada.")
        sys.exit(2)

    # 4. Ejecutar comando
    env = os.environ.copy()
    env["DATABASE_URL"] = prod_url
    env["ARGENTUM_PRODUCCION_CONFIRMADA"] = "1"
    if solo_lectura:
        env["ARGENTUM_PRODUCCION_SOLO_LECTURA"] = "1"
    else:
        env.pop("ARGENTUM_PRODUCCION_SOLO_LECTURA", None)

    t0 = time.time()
    res = subprocess.run(args, env=env)
    duracion = time.time() - t0

    # 5. Registrar ejecución en registro_produccion.log
    fecha_ar = datetime.now(timezone(timedelta(hours=-3))).strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"{fecha_ar} | {modo} | {comando_str} | codigo: {res.returncode} | duracion: {duracion:.2f}s\n"
    log_file = rutas.auditorias_dir() / "registro_produccion.log"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

    sys.exit(res.returncode)


if __name__ == "__main__":
    main()
