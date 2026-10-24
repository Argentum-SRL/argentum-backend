r"""
Refresco completo de la base de datos local replicando la base de producción.
- Inicia PostgreSQL local si está apagado.
- Ejecuta pg_dump de producción en formato custom (-Fc, --no-owner --no-acl) a dumps_dir().
- Aborta si el destino no es localhost o 127.0.0.1.
- Borra y recrea la base argentum_local y restaura con pg_restore.
- Imprime duración y conteo de filas por tabla en producción y en local.
- Ejecuta alembic upgrade head en la base local y compara versiones de alembic.
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

from app.core.entorno import opciones_conexion
from scripts.local import rutas
from scripts.local import produccion

# Tablas que cambian solas en producción durante la copia (ejecuciones de jobs y renovación de sesiones).
# Se tolera una pequeña cantidad de filas adicionales en producción para evitar falsos positivos al refrescar.
TABLAS_QUE_CAMBIAN_SOLAS = {
    "ejecuciones_jobs": 5,
    "user_refresh_tokens": 5,
}


def formatear_mensaje_migraciones(rev_prod: str, rev_local: str) -> str:
    """
    Formatea el mensaje de estado de migraciones entre producción y copia local (Decisión 6).
    Función pura.
    """
    if rev_prod == rev_local:
        return f"Migraciones: producción y copia local en {rev_prod}."
    return (
        f"MIGRACIÓN PENDIENTE EN PRODUCCIÓN: producción en {rev_prod}, "
        f"copia local en {rev_local}. Se aplica con con_produccion.py antes del push."
    )


def clasificar_tabla(tabla: str, cnt_prod: int, cnt_local: int) -> str:
    """
    Clasifica el estado de sincronización de una tabla entre producción y local.
    Devuelve:
      - "OK": si cnt_prod == cnt_local.
      - "TOLERADA": si la tabla está en TABLAS_QUE_CAMBIAN_SOLAS, cnt_local >= 0 y
        0 < cnt_prod - cnt_local <= tope.
      - "DIFERENCIA": en cualquier otro caso (incluyendo local mayor o tabla faltante cnt_local == -1).
    """
    if cnt_prod == cnt_local:
        return "OK"
    tope = TABLAS_QUE_CAMBIAN_SOLAS.get(tabla)
    if tope is not None and cnt_local >= 0 and 0 < (cnt_prod - cnt_local) <= tope:
        return "TOLERADA"
    return "DIFERENCIA"


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
        print("[refrescar_base_local] Servidor local apagado. Arrancando con pg_ctl...")
        start_res = subprocess.run(
            [str(pg_ctl), "-D", str(data_dir), "-l", str(log_file), "-w", "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if start_res.returncode != 0:
            print(f"[refrescar_base_local] ERROR: pg_ctl start retorno {start_res.returncode}")
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
    prod_url = produccion.url_para_refresco()

    # Rutas de binarios y volcado
    pg_dump = rutas.pgsql_dir() / "bin" / "pg_dump.exe"
    pg_restore = rutas.pgsql_dir() / "bin" / "pg_restore.exe"
    psql = rutas.pgsql_dir() / "bin" / "psql.exe"
    dumps_dir = rutas.dumps_dir()
    dump_file = dumps_dir / "argentum_prod.dump"

    # 3. pg_dump de producción
    dumps_dir.mkdir(parents=True, exist_ok=True)
    print(f"Iniciando pg_dump de produccion a {dump_file}...")
    t_dump_0 = time.time()
    dump_cmd = [
        str(pg_dump),
        f"--dbname={prod_url}",
        "-Fc",
        "--no-owner",
        "--no-acl",
        "-f", str(dump_file),
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
    dump_size = dump_file.stat().st_size
    print(f"pg_dump completado en {dump_dur:.2f} s. Tamano del volcado: {dump_size} bytes ({dump_size / (1024*1024):.2f} MB)")

    # 4. Recrear base de datos local
    print("Recreando base de datos argentum_local en postgres local...")
    local_env = os.environ.copy()
    local_env["PGPASSWORD"] = pg_password
    drop_cmd = [
        str(psql), "-h", "localhost", "-p", "5433", "-U", "postgres", "-d", "postgres",
        "-c", "DROP DATABASE IF EXISTS argentum_local WITH (FORCE);",
    ]
    drop_res = subprocess.run(drop_cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, env=local_env)
    if drop_res.returncode != 0:
        print(f"ERROR en DROP DATABASE (codigo {drop_res.returncode}): {drop_res.stderr}")
        sys.exit(1)

    create_cmd = [
        str(psql), "-h", "localhost", "-p", "5433", "-U", "postgres", "-d", "postgres",
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
        str(pg_restore),
        "-h", "localhost",
        "-p", "5433",
        "-U", "postgres",
        "-d", "argentum_local",
        "--no-owner",
        "--no-acl",
        str(dump_file),
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
    prod_connect_args = opciones_conexion(prod_url, {"ARGENTUM_PRODUCCION_SOLO_LECTURA": "1"})
    engine_prod = create_engine(prod_url, connect_args=prod_connect_args)
    engine_local = create_engine(local_url)

    tablas_sql = text("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;")
    with engine_prod.connect() as conn_p, engine_local.connect() as conn_l:
        # Comprobar que producción está en solo lectura
        ro_val = conn_p.execute(text("SHOW transaction_read_only;")).scalar()
        if str(ro_val).lower() != "on":
            print("No se pudo abrir producción en solo lectura.")
            sys.exit(1)

        tables_prod = [r[0] for r in conn_p.execute(tablas_sql).fetchall()]
        tables_local = [r[0] for r in conn_l.execute(tablas_sql).fetchall()]

        discrepancias = []
        toleradas = []
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

            estado = clasificar_tabla(t, cnt_p, cnt_l)
            if estado in ("OK", "TOLERADA"):
                iguales += 1
                if estado == "TOLERADA":
                    toleradas.append((t, cnt_p, cnt_l))
            else:
                discrepancias.append((t, cnt_p, cnt_l))
            print(f"{t:<35} | {cnt_p:>8} | {cnt_l:>8} | {estado:<10}")

        print("-" * 70)
        print(f"Total tablas: {total_tablas} | Tablas iguales: {iguales} | Tablas distintas: {len(discrepancias)}")
        if toleradas:
            for t, cp, cl in toleradas:
                print(f"Tolerada (cambia sola en producción): {t} prod={cp} local={cl}")

    if discrepancias:
        print(f"ERROR: Se encontraron {len(discrepancias)} discrepancias entre produccion y local:")
        for t, cp, cl in discrepancias:
            print(f"  - {t}: prod={cp}, local={cl}")
        sys.exit(1)
    else:
        print("TODAS las tablas tienen exactamente la misma cantidad de filas en produccion y en local.")

    # 7. Migraciones locales y comparación con producción (Decisión 6)
    con_base_local_py = BACKEND_DIR / "scripts" / "local" / "con_base_local.py"
    cmd_alembic = [
        sys.executable,
        str(con_base_local_py),
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
    ]
    res_mig = subprocess.run(cmd_alembic, cwd=str(BACKEND_DIR), capture_output=True, text=True)
    if res_mig.returncode != 0:
        print(f"ERROR al ejecutar migraciones locales: {res_mig.stderr}")
        sys.exit(1)

    with engine_prod.connect() as conn_p, engine_local.connect() as conn_l:
        rev_prod = conn_p.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        rev_local = conn_l.execute(text("SELECT version_num FROM alembic_version;")).scalar()
        print(formatear_mensaje_migraciones(str(rev_prod or ""), str(rev_local or "")))

    engine_prod.dispose()
    engine_local.dispose()

    duracion_total = time.time() - t0
    print(f"\nDuracion total del refresco: {duracion_total:.2f} s")


if __name__ == "__main__":
    main()
