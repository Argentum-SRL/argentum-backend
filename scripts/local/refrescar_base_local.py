r"""
Refresco completo de la base de datos local replicando la base de producción.
- Inicia PostgreSQL local si está apagado.
- Ejecuta pg_dump de producción en formato custom (-Fc, --no-owner --no-acl) a C:\argentum_local\dumps\.
- Aborta si el destino no es localhost o 127.0.0.1.
- Borra y recrea la base argentum_local y restaura con pg_restore.
- Imprime duración y conteo de filas por tabla en producción y en local.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

PGSQL_DIR = Path(r"C:\argentum_local\pgsql")
DATA_DIR = Path(r"C:\argentum_local\data")
LOG_FILE = Path(r"C:\argentum_local\pg.log")
ENV_FILE = Path(r"C:\argentum_local\pg_local.env")
DUMPS_DIR = Path(r"C:\argentum_local\dumps")
DUMP_FILE = DUMPS_DIR / "argentum_prod.dump"

PG_CTL = PGSQL_DIR / "bin" / "pg_ctl.exe"
PG_DUMP = PGSQL_DIR / "bin" / "pg_dump.exe"
PG_RESTORE = PGSQL_DIR / "bin" / "pg_restore.exe"
PSQL = PGSQL_DIR / "bin" / "psql.exe"

def asegurar_servidor_levantado():
    if not PG_CTL.exists():
        raise FileNotFoundError(f"No existe pg_ctl en {PG_CTL}")

    res = subprocess.run(
        [str(PG_CTL), "-D", str(DATA_DIR), "status"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print("[refrescar_base_local] Servidor local apagado. Arrancando con pg_ctl...")
        start_res = subprocess.run(
            [str(PG_CTL), "-D", str(DATA_DIR), "-l", str(LOG_FILE), "-w", "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if start_res.returncode != 0:
            print(f"[refrescar_base_local] ERROR: pg_ctl start retorno {start_res.returncode}")
            sys.exit(1)
        time.sleep(1)

def obtener_prod_database_url():
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        raise FileNotFoundError(f"No existe {env_file}")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ValueError(f"DATABASE_URL no encontrada en {env_file}")

def obtener_credenciales_locales():
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"No existe {ENV_FILE}")
    env_vars = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars

def main():
    t0 = time.time()
    print("=== REFRESCAR BASE LOCAL ARGENTUM ===")

    # 1. Servidor levantado
    asegurar_servidor_levantado()

    # 2. Control de destino de seguridad (PROHIBIDO sobreescribir producción)
    dest_host = "localhost"
    dest_port = 5433
    dest_db = "argentum_local"
    if dest_host not in ("localhost", "127.0.0.1") or dest_port != 5433:
        print(f"ABORTADO POR SEGURIDAD: Destino no seguro ({dest_host}:{dest_port})")
        sys.exit(1)
    print(f"Destino validado: {dest_host}:{dest_port}/{dest_db} (local)")

    local_vars = obtener_credenciales_locales()
    pg_password = local_vars.get("PGPASSWORD", "")
    prod_url = obtener_prod_database_url()

    # 3. pg_dump de producción
    DUMPS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Iniciando pg_dump de produccion a {DUMP_FILE}...")
    t_dump_0 = time.time()
    dump_cmd = [
        str(PG_DUMP),
        f"--dbname={prod_url}",
        "-Fc",
        "--no-owner",
        "--no-acl",
        "-f", str(DUMP_FILE),
    ]
    dump_res = subprocess.run(
        dump_cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if dump_res.returncode != 0:
        print(f"ERROR en pg_dump (codigo {dump_res.returncode}): {dump_res.stderr}")
        sys.exit(1)
    dump_dur = time.time() - t_dump_0
    dump_size = DUMP_FILE.stat().st_size
    print(f"pg_dump completado en {dump_dur:.2f} s. Tamano del volcado: {dump_size} bytes ({dump_size / (1024*1024):.2f} MB)")

    # 4. Recrear base de datos local
    print("Recreando base de datos argentum_local en postgres local...")
    local_env = os.environ.copy()
    local_env["PGPASSWORD"] = pg_password
    drop_cmd = [
        str(PSQL), "-h", "localhost", "-p", "5433", "-U", "postgres", "-d", "postgres",
        "-c", "DROP DATABASE IF EXISTS argentum_local WITH (FORCE);",
    ]
    drop_res = subprocess.run(drop_cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, env=local_env)
    if drop_res.returncode != 0:
        print(f"ERROR en DROP DATABASE (codigo {drop_res.returncode}): {drop_res.stderr}")
        sys.exit(1)

    create_cmd = [
        str(PSQL), "-h", "localhost", "-p", "5433", "-U", "postgres", "-d", "postgres",
        "-c", "CREATE DATABASE argentum_local WITH LOCALE_PROVIDER = icu ICU_LOCALE = 'en-US' ENCODING = 'UTF8';",
    ]
    create_res = subprocess.run(create_cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, env=local_env)
    if create_res.returncode != 0:
        print(f"ERROR en CREATE DATABASE (codigo {create_res.returncode}): {create_res.stderr}")
        sys.exit(1)
    print("Base argentum_local recreada con exito.")

    # 5. pg_restore
    print(f"Restaurando volcado con pg_restore en argentum_local...")
    t_rest_0 = time.time()
    restore_cmd = [
        str(PG_RESTORE),
        "-h", "localhost",
        "-p", "5433",
        "-U", "postgres",
        "-d", "argentum_local",
        "--no-owner",
        "--no-acl",
        str(DUMP_FILE),
    ]
    restore_res = subprocess.run(
        restore_cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=local_env,
    )
    # pg_restore puede retornar 1 si hay advertencias no críticas
    if restore_res.returncode > 1:
        print(f"ERROR critico en pg_restore (codigo {restore_res.returncode}): {restore_res.stderr}")
        sys.exit(1)
    rest_dur = time.time() - t_rest_0
    print(f"pg_restore completado en {rest_dur:.2f} s.")

    # 6. Conteo y comparación de filas por tabla entre producción y local
    print("\n--- COMPARACION DE FILAS POR TABLA (PRODUCCION vs LOCAL) ---")
    from sqlalchemy import create_engine, text

    local_url = f"postgresql://postgres:{pg_password}@localhost:5433/argentum_local"
    engine_prod = create_engine(prod_url)
    engine_local = create_engine(local_url)

    tablas_sql = text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;")
    with engine_prod.connect() as conn_p, engine_local.connect() as conn_l:
        tables_prod = [r[0] for r in conn_p.execute(tablas_sql).fetchall()]
        tables_local = [r[0] for r in conn_l.execute(tablas_sql).fetchall()]

        discrepancias = []
        iguales = 0
        total_tablas = len(tables_prod)

        print(f"{'Tabla':<35} | {'Prod':>8} | {'Local':>8} | {'Estado':<10}")
        print("-" * 70)
        for t in tables_prod:
            cnt_p = conn_p.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
            if t in tables_local:
                cnt_l = conn_l.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
            else:
                cnt_l = -1

            match = cnt_p == cnt_l
            estado = "OK" if match else "DIFERENCIA"
            if match:
                iguales += 1
            else:
                discrepancias.append((t, cnt_p, cnt_l))
            print(f"{t:<35} | {cnt_p:>8} | {cnt_l:>8} | {estado:<10}")

        print("-" * 70)
        print(f"Total tablas: {total_tablas} | Tablas iguales: {iguales} | Tablas distintas: {len(discrepancias)}")

    duracion_total = time.time() - t0
    print(f"\nDuracion total del refresco: {duracion_total:.2f} s")

    if discrepancias:
        print(f"ERROR: Se encontraron {len(discrepancias)} discrepancias entre produccion y local:")
        for t, cp, cl in discrepancias:
            print(f"  - {t}: prod={cp}, local={cl}")
        sys.exit(1)
    else:
        print("TODAS las tablas tienen exactamente la misma cantidad de filas en produccion y en local.")

if __name__ == "__main__":
    main()
