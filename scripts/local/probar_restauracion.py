# -*- coding: utf-8 -*-
"""
Prueba de restauración de backup (TJ.8).
Restaura el último volcado de producción (argentum_prod.dump) en una base nueva
y temporal (argentum_restauracion_prueba) en PostgreSQL local, compara los conteos
tabla por tabla contra argentum_local, arranca la app sin lifespan ni scheduler
para verificar GET /salud y conteos básicos, y elimina la base temporal.
Nunca toca argentum_local para escritura ni toca producción.
"""
from __future__ import annotations

import os
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.utils.fecha import TZ_ARGENTINA
from scripts.local import rutas

BASE = "argentum_restauracion_prueba"
HOST = "localhost"
PORT = 5433

# Tablas que la app y sus tareas programadas escriben solas mientras se usa la copia local,
# así que pueden cambiar después del refresco; las tablas con datos de las personas
# (usuarios, transacciones, billeteras, cuotas, metas, presupuestos y el resto) tienen que dar IGUAL.
TABLAS_DE_USO = frozenset(
    {
        "rate_limits",
        "eventos_actualizacion",
        "mensajes_salientes_wpp",
        "mensajes_whatsapp_procesados",
        "conversaciones_wpp",
        "uso_ia",
        "ejecuciones_jobs",
        "user_refresh_tokens",
        "codigos_verificacion",
        "claves_idempotencia",
        "eventos_uso",
        "notificaciones",
        "cotizaciones_dolar",
    }
)


def clasificar_tabla(nombre: str, cantidad_restaurada: int, cantidad_local: int) -> str:
    """Clasifica la diferencia de filas entre la base restaurada y la copia local."""
    if cantidad_restaurada == cantidad_local:
        return "IGUAL"
    if nombre in TABLAS_DE_USO:
        return "DISTINTA POR USO"
    return "DISTINTA"


def _verificar_guarda_base_local() -> None:
    """Guarda obligatoria de REGLAS 1: solo corre si DATABASE_URL apunta a localhost:5433."""
    if "localhost:5433" not in os.environ.get("DATABASE_URL", ""):
        print("FRENO: falta con_base_local.py")
        sys.exit(1)


def _leer_credenciales_locales() -> dict[str, str]:
    env_file = rutas.pg_local_env()
    if not env_file.exists():
        raise FileNotFoundError(f"No existe {env_file}")
    env_vars: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")
    return env_vars


def _run_psql(psql: Path, db_name: str, sql: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    cmd = [
        str(psql),
        "-h", HOST,
        "-p", str(PORT),
        "-U", "postgres",
        "-d", db_name,
        "-At",
        "-v", "ON_ERROR_STOP=1",
        "-c", sql,
    ]
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _modo_humo() -> int:
    _verificar_guarda_base_local()
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select
    from app.core.database import SessionLocal
    from app.main import app
    from app.models.transaccion import Transaccion
    from app.models.usuario import Usuario

    # TestClient SIN "with": no arranca lifespan ni scheduler contra la base restaurada
    client = TestClient(app)
    resp = client.get("/salud")
    print(f"/salud status={resp.status_code} cuerpo={resp.text.strip()}")

    db = SessionLocal()
    try:
        cnt_usuarios = db.execute(select(func.count()).select_from(Usuario)).scalar_one()
        cnt_tx = db.execute(select(func.count()).select_from(Transaccion)).scalar_one()
        print(f"Conteo en restaurada -> usuarios={cnt_usuarios}, transacciones={cnt_tx}")
    finally:
        db.close()

    return 0 if resp.status_code == 200 else 1


def main() -> int:
    if "--humo" in sys.argv[1:]:
        return _modo_humo()

    _verificar_guarda_base_local()

    # Guarda de seguridad sobre destino
    if BASE != "argentum_restauracion_prueba" or HOST not in ("localhost", "127.0.0.1") or PORT != 5433:
        print(f"ABORTADO: destino no permitido ({HOST}:{PORT}/{BASE})")
        return 2

    local_vars = _leer_credenciales_locales()
    pg_password = local_vars.get("PGPASSWORD", "")
    pg_env = os.environ.copy()
    pg_env["PGPASSWORD"] = pg_password

    psql = rutas.pgsql_dir() / "bin" / "psql.exe"
    pg_restore = rutas.pgsql_dir() / "bin" / "pg_restore.exe"
    dump_file = rutas.dumps_dir() / "argentum_prod.dump"

    restore_rc = -1
    tablas_iguales = False
    solo_un_lado_vacio = False
    humo_ok = False
    borrada = False
    motivo_fallo = ""

    print(f"=== PRUEBA DE RESTAURACION (TJ.8) -> {HOST}:{PORT}/{BASE} ===")

    # Paso 1: Volcado
    t0 = time.perf_counter()
    if not dump_file.exists():
        print(f"[1. Volcado] FALLO: no existe {dump_file} ({time.perf_counter() - t0:.2f}s)")
        print("RESTAURACIÓN: FALLÓ: no existe argentum_prod.dump")
        return 1
    st = dump_file.stat()
    size_mb = st.st_size / (1024 * 1024)
    mtime_art = datetime.fromtimestamp(st.st_mtime, tz=TZ_ARGENTINA)
    print(
        f"[1. Volcado] OK ({time.perf_counter() - t0:.2f}s): "
        f"ruta={dump_file} | tamano={size_mb:.2f} MB | modificacion={mtime_art.isoformat()}"
    )

    try:
        # Paso 2: Recrear base temporal
        t1 = time.perf_counter()
        res_drop = _run_psql(psql, "postgres", f"DROP DATABASE IF EXISTS {BASE} WITH (FORCE);", pg_env)
        if res_drop.returncode != 0:
            motivo_fallo = f"DROP DATABASE fallo (rc={res_drop.returncode})"
            print(f"[2. Crear base] FALLO ({time.perf_counter() - t1:.2f}s): {res_drop.stderr.strip()}")
            return 1

        res_create = _run_psql(
            psql,
            "postgres",
            f"CREATE DATABASE {BASE} WITH LOCALE_PROVIDER = icu ICU_LOCALE = 'en-US' ENCODING = 'UTF8';",
            pg_env,
        )
        if res_create.returncode != 0:
            motivo_fallo = f"CREATE DATABASE fallo (rc={res_create.returncode})"
            print(f"[2. Crear base] FALLO ({time.perf_counter() - t1:.2f}s): {res_create.stderr.strip()}")
            return 1
        print(f"[2. Crear base] OK ({time.perf_counter() - t1:.2f}s): {BASE} creada.")

        # Paso 3: pg_restore
        t2 = time.perf_counter()
        restore_cmd = [
            str(pg_restore),
            "-h", HOST,
            "-p", str(PORT),
            "-U", "postgres",
            "-d", BASE,
            "--no-owner",
            "--no-acl",
            str(dump_file),
        ]
        res_rest = subprocess.run(
            restore_cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=pg_env,
        )
        restore_rc = res_rest.returncode
        dt_rest = time.perf_counter() - t2
        if restore_rc > 1:
            motivo_fallo = f"pg_restore rc={restore_rc}"
            print(f"[3. pg_restore] FALLO ({dt_rest:.2f}s) rc={restore_rc}")
            if res_rest.stderr.strip():
                print(res_rest.stderr.strip())
            return 1
        print(f"[3. pg_restore] OK ({dt_rest:.2f}s) rc={restore_rc}")
        if restore_rc == 1 and res_rest.stderr.strip():
            print("Advertencias de pg_restore:")
            print(res_rest.stderr.strip())

        # Paso 4: Comparación de tablas
        t3 = time.perf_counter()
        sql_tablas = (
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name;"
        )
        res_t_rest = _run_psql(psql, BASE, sql_tablas, pg_env)
        res_t_loc = _run_psql(psql, "argentum_local", sql_tablas, pg_env)
        if res_t_rest.returncode != 0 or res_t_loc.returncode != 0:
            motivo_fallo = "No se pudo listar tablas"
            print(f"[4. Comparacion] FALLO al listar tablas ({time.perf_counter() - t3:.2f}s)")
            return 1

        tablas_rest = [t.strip() for t in res_t_rest.stdout.splitlines() if t.strip()]
        tablas_loc = [t.strip() for t in res_t_loc.stdout.splitlines() if t.strip()]
        set_rest = set(tablas_rest)
        set_loc = set(tablas_loc)
        comunes = sorted(set_rest & set_loc)
        solo_rest = sorted(set_rest - set_loc)
        solo_loc = sorted(set_loc - set_rest)

        distintas: list[str] = []
        distintas_por_uso: list[str] = []
        print(f"[4. Comparacion] ({len(comunes)} tablas comunes):")
        print(f"{'Tabla':<35} | {'Restaurada':>10} | {'Local':>10} | {'Estado':<16}")
        print("-" * 80)
        for tabla in comunes:
            q_cnt = f'SELECT count(*) FROM "{tabla}";'
            c_rest = int(_run_psql(psql, BASE, q_cnt, pg_env).stdout.strip())
            c_loc = int(_run_psql(psql, "argentum_local", q_cnt, pg_env).stdout.strip())
            estado = clasificar_tabla(tabla, c_rest, c_loc)
            if estado == "DISTINTA":
                distintas.append(tabla)
            elif estado == "DISTINTA POR USO":
                distintas_por_uso.append(tabla)
            print(f"{tabla:<35} | {c_rest:>10} | {c_loc:>10} | {estado:<16}")
        print("-" * 80)
        print(f"Solo en restaurada ({len(solo_rest)}): {solo_rest}")
        print(f"Solo en local ({len(solo_loc)}): {solo_loc}")
        uso_lista = ", ".join(distintas_por_uso) if distintas_por_uso else "ninguna"
        print(f"Distintas por uso de la copia local (se aceptan): {uso_lista}")
        tablas_iguales = len(distintas) == 0
        solo_un_lado_vacio = (len(solo_rest) == 0 and len(solo_loc) == 0)
        print(
            f"[4. Comparacion] Fin ({time.perf_counter() - t3:.2f}s): "
            f"distintas={len(distintas)}, distintas_por_uso={len(distintas_por_uso)}"
        )

        # Paso 5: Versión de alembic en restaurada
        t4 = time.perf_counter()
        res_alembic = _run_psql(psql, BASE, "SELECT version_num FROM alembic_version;", pg_env)
        ver_rest = res_alembic.stdout.strip()
        print(f"[5. Alembic restaurada] ({time.perf_counter() - t4:.2f}s): {ver_rest}")

        # Paso 6: Arranque de la app (--humo) contra la base restaurada
        t5 = time.perf_counter()
        humo_env = os.environ.copy()
        humo_env["DATABASE_URL"] = f"postgresql://postgres:{pg_password}@{HOST}:{PORT}/{BASE}"
        humo_env["PGHOST"] = HOST
        humo_env["PGPORT"] = str(PORT)
        humo_env["PGUSER"] = "postgres"
        humo_env["PGPASSWORD"] = pg_password
        humo_env["PGDATABASE"] = BASE
        res_humo = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--humo"],
            cwd=str(BACKEND_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=humo_env,
        )
        humo_ok = res_humo.returncode == 0
        print(f"[6. Arranque app --humo] ({time.perf_counter() - t5:.2f}s) rc={res_humo.returncode}:")
        if res_humo.stdout.strip():
            print(res_humo.stdout.strip())
        if res_humo.stderr.strip():
            print(res_humo.stderr.strip())
    finally:
        # Paso 7: Borrado de base temporal y verificación
        t6 = time.perf_counter()
        _run_psql(psql, "postgres", f"DROP DATABASE IF EXISTS {BASE} WITH (FORCE);", pg_env)
        res_check = _run_psql(
            psql,
            "postgres",
            f"SELECT 1 FROM pg_database WHERE datname = '{BASE}';",
            pg_env,
        )
        borrada = (res_check.returncode == 0 and res_check.stdout.strip() == "")
        print(f"[7. Limpieza] ({time.perf_counter() - t6:.2f}s) BASE TEMPORAL BORRADA: {'SÍ' if borrada else 'NO'}")

    # Paso 8: Veredicto final
    ok = (
        restore_rc in (0, 1)
        and tablas_iguales
        and solo_un_lado_vacio
        and humo_ok
        and borrada
    )
    if ok:
        print("RESTAURACIÓN: BIEN")
        return 0

    if not motivo_fallo:
        razones = []
        if restore_rc not in (0, 1):
            razones.append(f"restore_rc={restore_rc}")
        if not tablas_iguales:
            razones.append("tablas distintas")
        if not solo_un_lado_vacio:
            razones.append("tablas solo de un lado")
        if not humo_ok:
            razones.append("humo /salud fallo")
        if not borrada:
            razones.append("base temporal no borrada")
        motivo_fallo = ", ".join(razones)
    print(f"RESTAURACIÓN: FALLÓ: {motivo_fallo}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
