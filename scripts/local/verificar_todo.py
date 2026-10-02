"""
Script integrador verificar_todo.py:
1. Inicia PostgreSQL local si está apagado.
2. Refresca la base local (salvo que se pase --sin-refrescar).
3. Corre pytest -q contra base local.
4. Corre la suite completa de WhatsApp contra base local.
5. Corre el verificador de testingadmin contra base local.
Imprime resultado y duración de cada paso y duración total.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parents[2]
LOCAL_SCRIPTS_DIR = BACKEND_DIR / "scripts" / "local"
PYTHON_EXE = Path(sys.executable)

PGSQL_DIR = Path(r"C:\argentum_local\pgsql")
DATA_DIR = Path(r"C:\argentum_local\data")
LOG_FILE = Path(r"C:\argentum_local\pg.log")
PG_CTL = PGSQL_DIR / "bin" / "pg_ctl.exe"

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
        print("[verificar_todo] Servidor local apagado. Arrancando con pg_ctl...")
        start_res = subprocess.run(
            [str(PG_CTL), "-D", str(DATA_DIR), "-l", str(LOG_FILE), "-w", "start"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if start_res.returncode != 0:
            print(f"[verificar_todo] ERROR: pg_ctl start retorno {start_res.returncode}")
            sys.exit(1)
        time.sleep(1)

def run_step(nombre: str, cmd_list: list[str]) -> tuple[bool, float]:
    print(f"\n{'='*20} PASO: {nombre} {'='*20}")
    t0 = time.time()
    res = subprocess.run(cmd_list, cwd=str(BACKEND_DIR))
    dur = time.time() - t0
    ok = (res.returncode == 0)
    print(f"[{'OK' if ok else 'FALLO'}] {nombre} terminado en {dur:.2f} s (retorno {res.returncode})")
    return ok, dur

def main():
    parser = argparse.ArgumentParser(description="Verificador integral con base local")
    parser.add_argument("--sin-refrescar", action="store_true", help="Omitir el refresco inicial de la base local")
    args = parser.parse_args()

    t_total_0 = time.time()
    print("=== INICIANDO VERIFICACION INTEGRAL (BASE LOCAL) ===")

    asegurar_servidor_levantado()

    con_base_local_py = LOCAL_SCRIPTS_DIR / "con_base_local.py"
    refrescar_py = LOCAL_SCRIPTS_DIR / "refrescar_base_local.py"
    suite_py = BACKEND_DIR / "scripts" / "regresion" / "suite_regresion_whatsapp.py"
    verificador_py = BACKEND_DIR / "scripts" / "testingadmin" / "verificar_testingadmin.py"

    resultados = []

    # 1. Refresco
    if not args.sin_refrescar:
        ok_ref, dur_ref = run_step("1. Refresco de base local", [str(PYTHON_EXE), str(refrescar_py)])
        resultados.append(("Refresco base local", ok_ref, dur_ref))
        if not ok_ref:
            print("ERROR: Fallo el refresco de base local. Se detiene la verificacion.")
            sys.exit(1)
    else:
        print("\n[OMITIDO] Refresco de base local por parametro --sin-refrescar.")

    # 2. Pytest
    cmd_pytest = [str(PYTHON_EXE), str(con_base_local_py), str(PYTHON_EXE), "-m", "pytest", "-q"]
    ok_py, dur_py = run_step("2. Pytest contra base local", cmd_pytest)
    resultados.append(("Pytest (-q)", ok_py, dur_py))

    # 3. Suite WhatsApp
    cmd_suite = [str(PYTHON_EXE), str(con_base_local_py), str(PYTHON_EXE), str(suite_py), "-v", "--forzar-grabadas"]
    ok_suite, dur_suite = run_step("3. Suite WhatsApp contra base local", cmd_suite)
    resultados.append(("Suite WhatsApp (-v)", ok_suite, dur_suite))

    # 4. Verificador testingadmin
    cmd_verif = [str(PYTHON_EXE), str(con_base_local_py), str(PYTHON_EXE), str(verificador_py)]
    ok_verif, dur_verif = run_step("4. Verificador testingadmin contra base local", cmd_verif)
    resultados.append(("Verificador testingadmin", ok_verif, dur_verif))

    total_dur = time.time() - t_total_0

    print("\n" + "="*60)
    print("=== RESUMEN FINAL: VERIFICAR TODO ===")
    print(f"{'Paso':<35} | {'Estado':<10} | {'Duracion':>10}")
    print("-" * 60)
    todo_ok = True
    for paso, ok, dur in resultados:
        st = "OK" if ok else "FALLO"
        if not ok:
            todo_ok = False
        print(f"{paso:<35} | {st:<10} | {dur:>8.2f} s")
    print("-" * 60)
    print(f"{'TIEMPO TOTAL':<35} | {'OK' if todo_ok else 'FALLO':<10} | {total_dur:>8.2f} s")
    print("="*60)

    if not todo_ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
