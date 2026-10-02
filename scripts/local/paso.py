"""
Herramienta fija de inicio y cierre de pasos de Argentum.
Ubicación: scripts/local/paso.py

Uso:
  venv\\Scripts\\python.exe scripts\\local\\paso.py inicio <paso>
  venv\\Scripts\\python.exe scripts\\local\\paso.py cierre <paso>
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

# Rutas estándar del proyecto
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
WORKSPACE_DIR = BACKEND_DIR.parent
FRONTEND_DIR = WORKSPACE_DIR / "argentum-frontend"
AUDITORIAS_DIR = WORKSPACE_DIR / "auditorias"
RAW_DIR_BASE = AUDITORIAS_DIR / "raw"
BACKUPS_DIR_BASE = AUDITORIAS_DIR / "backups"
FOTOS_DIR = AUDITORIAS_DIR / "fotos"
PYTHON_EXE = BACKEND_DIR / "venv" / "Scripts" / "python.exe"
if not PYTHON_EXE.exists():
    PYTHON_EXE = Path(sys.executable)

CON_BASE_LOCAL_PY = BACKEND_DIR / "scripts" / "local" / "con_base_local.py"
REFRESCAR_PY = BACKEND_DIR / "scripts" / "local" / "refrescar_base_local.py"
FOTO_MOTOR_PY = BACKEND_DIR / "scripts" / "motor" / "foto_motor.py"
COMPARAR_FOTOS_PY = BACKEND_DIR / "scripts" / "motor" / "comparar_fotos.py"
EVALUAR_PERSONAS_PY = BACKEND_DIR / "scripts" / "motor" / "evaluar_personas.py"
VERIFICAR_TESTINGADMIN_PY = BACKEND_DIR / "scripts" / "testingadmin" / "verificar_testingadmin.py"
SUITE_PY = BACKEND_DIR / "scripts" / "regresion" / "suite_regresion_whatsapp.py"
COMPARAR_SALIDAS_PY = BACKEND_DIR / "scripts" / "regresion" / "comparar_salidas.py"
VERIFICAR_TODO_PY = BACKEND_DIR / "scripts" / "local" / "verificar_todo.py"

REQUIRED_CANARIO_EMAILS = [
    "albanopavia@gmail.com",
    "angieperiolo@hotmail.com",
    "benitezsantiago2001@gmail.com",
    "giordaninosebas@gmail.com",
    "mrm291201@gmail.com",
    "orlandodjsegovia@gmail.com",
    "testingadmin@argentum.com",
]

ALERT_PATTERN = re.compile(r"\b(FALLO|DESVIO|ERROR|INESPERADA)\b")


# =============================================================================
# FUNCIONES PURAS Y UTILIDADES (TESTEABLES DE FORMA AISLADA)
# =============================================================================

def obtener_fecha_hora_argentina() -> tuple[str, str]:
    """Retorna (fecha_ar_YYYY_MM_DD, fecha_hora_completa)."""
    tz_ar = timezone(timedelta(hours=-3))
    now = datetime.now(tz_ar)
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S %z")


def verificar_misma_fecha(fecha_inicio: str, fecha_cierre: str) -> bool:
    """Verifica que la fecha de Argentina sea la misma."""
    return fecha_inicio.strip() == fecha_cierre.strip()


def clasificar_estado_git(status_porcelain: str, rev_count_ahead: int) -> str:
    """Clasifica el estado git: CAMBIOS SIN COMITEAR, COMMITS SIN PUSH o SINCRONIZADO."""
    if status_porcelain.strip():
        return "CAMBIOS SIN COMITEAR"
    elif rev_count_ahead > 0:
        return "COMMITS SIN PUSH"
    else:
        return "SINCRONIZADO"


def extraer_simbolos_ast(code: str) -> tuple[dict[str, str], list[str]]:
    """
    Extrae de un código Python:
    1. Diccionario de funciones y métodos calificados -> ast.dump sin atributos.
       (e.g., 'mi_func', 'Clase.metodo', 'func.interna', 'Clase.metodo.interna')
    2. Lista de ast.dump sin atributos de sentencias a nivel módulo
       (imports, constantes, asignaciones, expresiones, y clases sin los cuerpos de sus métodos).
    """
    try:
        tree = ast.parse(code)
    except Exception:
        return {}, []

    funcs: dict[str, str] = {}

    def visitar_funcs(node: ast.AST, prefijo: str = "") -> None:
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            return
        for child in body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefijo}.{child.name}" if prefijo else child.name
                funcs[qname] = ast.dump(child, include_attributes=False)
                visitar_funcs(child, qname)
            elif isinstance(child, ast.ClassDef):
                qname = f"{prefijo}.{child.name}" if prefijo else child.name
                visitar_funcs(child, qname)
            else:
                # Recorrer bloques internos como if, try, with
                visitar_funcs(child, prefijo)
                orelse = getattr(child, "orelse", None)
                if isinstance(orelse, list):
                    visitar_funcs(child, prefijo)

    visitar_funcs(tree, "")

    # Sentencias a nivel módulo
    mod_dumps: list[str] = []
    for child in tree.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        elif isinstance(child, ast.ClassDef):
            # Normalizar ClassDef extrayendo sentencias que no sean métodos
            # para que cambios en métodos no alteren el nivel módulo
            body_no_funcs = [s for s in child.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if not body_no_funcs:
                body_no_funcs = [ast.Pass()]
            class_copy = ast.ClassDef(
                name=child.name,
                bases=child.bases,
                keywords=child.keywords,
                body=body_no_funcs,
                decorator_list=child.decorator_list,
            )
            mod_dumps.append(ast.dump(class_copy, include_attributes=False))
        else:
            mod_dumps.append(ast.dump(child, include_attributes=False))

    return funcs, mod_dumps


def comparar_ast_archivo(code_antes: str | None, code_despues: str | None) -> dict[str, str]:
    """
    Compara dos versiones de un archivo Python a nivel AST.
    Retorna: {simbolo: 'IGUAL' | 'CAMBIADA' | 'NUEVA' | 'BORRADA'}
    """
    if code_antes is None and code_despues is None:
        return {}

    if code_antes is None:
        funcs_desp, mod_desp = extraer_simbolos_ast(code_despues or "")
        resultado = {k: "NUEVA" for k in funcs_desp}
        if mod_desp:
            resultado["<modulo>"] = "NUEVA"
        return resultado

    if code_despues is None:
        funcs_antes, mod_antes = extraer_simbolos_ast(code_antes or "")
        resultado = {k: "BORRADA" for k in funcs_antes}
        if mod_antes:
            resultado["<modulo>"] = "BORRADA"
        return resultado

    funcs_antes, mod_antes = extraer_simbolos_ast(code_antes)
    funcs_desp, mod_desp = extraer_simbolos_ast(code_despues)

    resultado = {}
    todas_funcs = sorted(list(set(funcs_antes.keys()) | set(funcs_desp.keys())))
    for f in todas_funcs:
        if f not in funcs_antes:
            resultado[f] = "NUEVA"
        elif f not in funcs_desp:
            resultado[f] = "BORRADA"
        else:
            resultado[f] = "IGUAL" if funcs_antes[f] == funcs_desp[f] else "CAMBIADA"

    # Comparación a nivel módulo
    if mod_antes or mod_desp:
        resultado["<modulo>"] = "IGUAL" if mod_antes == mod_desp else "CAMBIADA"

    return resultado


def parse_permitidas(permitidas_texto: str) -> set[tuple[str, str]]:
    """Parsea el archivo permitidas.txt retornando un conjunto de tuplas (ruta_norm, elemento)."""
    rules = set()
    for line in permitidas_texto.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "::" in line:
            parts = line.split("::", 1)
            ruta = parts[0].strip().replace("\\", "/")
            elem = parts[1].strip()
            rules.add((ruta, elem))
        else:
            ruta = line.replace("\\", "/")
            rules.add((ruta, "*"))
    return rules


def verificar_permitidas(
    cambios_por_archivo: dict[str, list[tuple[str, str]]],
    permitidas_texto: str | None,
) -> tuple[bool, list[str]]:
    """
    Verifica que todo lo CAMBIADA, NUEVA o BORRADA esté en permitidas.txt.
    Retorna: (todo_ok, fuera_de_lista).
    """
    if permitidas_texto is None:
        return False, ["FALLO: permitidas.txt no existe"]

    rules = parse_permitidas(permitidas_texto)
    fuera_de_lista: list[str] = []

    for ruta, elementos in cambios_por_archivo.items():
        ruta_norm = ruta.replace("\\", "/")
        wildcard_covered = (ruta_norm, "*") in rules

        for elem, estado in elementos:
            if estado == "IGUAL":
                continue
            if wildcard_covered:
                continue
            if (ruta_norm, elem) in rules:
                continue
            fuera_de_lista.append(f"{ruta_norm}::{elem} ({estado})")

    return len(fuera_de_lista) == 0, fuera_de_lista


def ejecutar_pyflakes(code: str, filename: str = "temp.py") -> list[dict]:
    """Ejecuta pyflakes sobre un código en memoria."""
    try:
        from pyflakes import api, reporter, messages
    except ImportError:
        return [{"text": "pyflakes no instalado", "undefined": False, "raw": "pyflakes no instalado"}]

    class CollectReporter(reporter.Reporter):
        def __init__(self):
            self.records = []

        def unexpectedError(self, fn, msg):
            self.records.append({"text": f"unexpected error: {msg}", "undefined": False, "raw": msg})

        def syntaxError(self, fn, msg, lineno, offset, text):
            self.records.append({"text": f"syntax error: {msg}", "undefined": False, "raw": msg})

        def flake(self, msg_obj):
            msg_text = msg_obj.message % msg_obj.message_args
            is_undef = isinstance(msg_obj, messages.UndefinedName)
            self.records.append({
                "text": msg_text,
                "undefined": is_undef,
                "raw": str(msg_obj),
            })

    rep = CollectReporter()
    api.check(code, filename, rep)
    return rep.records


def comparar_mensajes_pyflakes(
    code_antes: str | None,
    code_despues: str,
    filename: str = "temp.py",
) -> tuple[list[str], list[str]]:
    """
    Compara mensajes de pyflakes entre antes y después, ignorando números de línea.
    Retorna: (mensajes_nuevos, mensajes_undefined_fallo).
    """
    records_antes = ejecutar_pyflakes(code_antes, filename) if code_antes else []
    records_desp = ejecutar_pyflakes(code_despues, filename)

    textos_antes = {r["text"] for r in records_antes}

    mensajes_nuevos: list[str] = []
    mensajes_undefined: list[str] = []

    for r in records_desp:
        if r["text"] not in textos_antes:
            mensajes_nuevos.append(r["raw"])
            if r["undefined"]:
                mensajes_undefined.append(r["raw"])

    return mensajes_nuevos, mensajes_undefined


def verificar_tamanio_lineas(
    lineas_por_archivo: dict[str, int],
    max_lineas: int = 1300,
) -> tuple[bool, list[tuple[str, int]]]:
    """Verifica que ningún archivo supere max_lineas."""
    excedidos = [(f, c) for f, c in lineas_por_archivo.items() if c > max_lineas]
    return len(excedidos) == 0, excedidos


def extraer_lineas_alerta(texto: str) -> list[str]:
    """Extrae líneas que contengan FALLO, DESVIO, ERROR o INESPERADA."""
    lineas = []
    for line in texto.splitlines():
        l_str = line.strip()
        if not l_str:
            continue
        if ALERT_PATTERN.search(l_str):
            lineas.append(l_str)
    return lineas


def comparar_lineas_alerta(texto_inicio: str, texto_cierre: str) -> list[str]:
    """Detecta líneas de alerta en el cierre que no aparecían en el inicio."""
    alerta_inicio = set(extraer_lineas_alerta(texto_inicio))
    alerta_cierre = extraer_lineas_alerta(texto_cierre)
    nuevas = [l for l in alerta_cierre if l not in alerta_inicio]
    return nuevas


# =============================================================================
# COMANDOS DEL SISTEMA Y OPERACIONES CON DISCO / REPOS
# =============================================================================

def obtener_hash_paso_py() -> str:
    """Obtiene el hash git (git hash-object) de este script paso.py."""
    script_path = Path(__file__).resolve()
    try:
        res = subprocess.run(
            ["git", "hash-object", str(script_path)],
            capture_output=True,
            text=True,
            cwd=str(BACKEND_DIR),
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "desconocido"


def generar_encabezado_crudo() -> str:
    """Genera el bloque inicial obligatorio para cada crudo."""
    hash_obj = obtener_hash_paso_py()
    _, fecha_hora = obtener_fecha_hora_argentina()
    return (
        f"Ruta: scripts/local/paso.py\n"
        f"Hash: {hash_obj}\n"
        f"Fecha y hora: {fecha_hora}\n"
        f"{'=' * 80}\n\n"
    )


def run_cmd(cmd: list[str] | str, cwd: Path = BACKEND_DIR, shell: bool = False) -> tuple[str, str, int]:
    """Ejecuta un comando en consola y captura stdout, stderr y returncode."""
    res = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=shell,
    )
    return res.stdout.strip(), res.stderr.strip(), res.returncode


def inspeccionar_git_repo(repo_dir: Path) -> dict[str, str | int]:
    """Inspecciona status, HEAD, origin/main y commits adelante de un repositorio."""
    out_st, _, _ = run_cmd(["git", "status", "--porcelain"], cwd=repo_dir)
    out_head, _, _ = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_dir)
    out_orig, _, _ = run_cmd(["git", "rev-parse", "origin/main"], cwd=repo_dir)
    out_cnt, _, _ = run_cmd(["git", "rev-list", "--count", "origin/main..HEAD"], cwd=repo_dir)

    try:
        cnt_ahead = int(out_cnt)
    except ValueError:
        cnt_ahead = 0

    clasif = clasificar_estado_git(out_st, cnt_ahead)
    return {
        "status_porcelain": out_st,
        "head": out_head,
        "origin_main": out_orig,
        "ahead": cnt_ahead,
        "clasificacion": clasif,
    }


def verificar_canario_bd() -> tuple[bool, str]:
    """Verifica que la base tenga exactamente 8 usuarios y los 7 correos canarios."""
    from sqlalchemy import create_engine, text

    # Obtener credenciales desde pg_local.env si existe
    env_file = Path(r"C:\argentum_local\pg_local.env")
    password = ""
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PGPASSWORD="):
                password = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

    db_url = os.environ.get("DATABASE_URL")
    if not db_url or "localhost:5433" not in db_url:
        db_url = f"postgresql://postgres:{password}@localhost:5433/argentum_local"

    try:
        engine = create_engine(db_url)
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT email FROM usuarios ORDER BY email")).fetchall()
            emails = [r[0] for r in rows]
    except Exception as e:
        return False, f"ERROR al conectar a base de datos: {e}"

    if len(emails) != 8:
        return False, f"Canario: Total de usuarios = {len(emails)} (esperado: 8). Emails: {emails}"

    faltantes = [req for req in REQUIRED_CANARIO_EMAILS if req not in emails]
    if faltantes:
        return False, f"Canario: Faltan los siguientes emails obligatorios: {faltantes}"

    return True, f"Canario OK: 8 usuarios y todos los 7 emails obligatorios presentes ({len(emails)} usuarios)."


def realizar_copia_segura(origen_base: Path, destino_base: Path, subcarpetas: list[str]) -> None:
    """Copia carpetas excluyendo __pycache__, .pyc y node_modules."""
    def _ignore(directory, contents):
        ign = set()
        for c in contents:
            if c == "__pycache__" or c == "node_modules" or c.endswith(".pyc"):
                ign.add(c)
        return ign

    for sub in subcarpetas:
        src = origen_base / sub
        dst = destino_base / sub
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, ignore=_ignore, dirs_exist_ok=True)


def escanear_archivos_monitoreados(base_dir: Path, subcarpetas: list[str]) -> dict[str, Path]:
    """Retorna {ruta_relativa: ruta_absoluta} de archivos en las subcarpetas monitoreadas."""
    archivos = {}
    for sub in subcarpetas:
        dir_path = base_dir / sub
        if not dir_path.exists():
            continue
        for root, dirs, files in os.walk(dir_path):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules")]
            for f in files:
                if f.endswith(".pyc"):
                    continue
                p = Path(root) / f
                rel = p.relative_to(base_dir).as_posix()
                archivos[rel] = p
    return archivos


# =============================================================================
# COMANDOS PRINCIPALES: INICIO Y CIERRE
# =============================================================================

def ejecutar_inicio(paso: str) -> int:
    raw_dir = RAW_DIR_BASE / paso
    backup_antes_dir = BACKUPS_DIR_BASE / paso / "antes"
    raw_dir.mkdir(parents=True, exist_ok=True)

    header = generar_encabezado_crudo()

    print(f"=== INICIANDO PASO: {paso} ===")
    buf_00 = io.StringIO()
    buf_00.write(header)

    fecha_ar, fecha_hora_ar = obtener_fecha_hora_argentina()
    buf_00.write(f"Fecha y hora de Argentina: {fecha_hora_ar}\n\n")

    # 1. Estado de git
    buf_00.write("--- 1. ESTADO DE GIT ---\n")
    git_be = inspeccionar_git_repo(BACKEND_DIR)
    git_fe = inspeccionar_git_repo(FRONTEND_DIR)

    buf_00.write(f"Backend (argentum-backend):\n")
    buf_00.write(f"  HEAD: {git_be['head']}\n")
    buf_00.write(f"  origin/main: {git_be['origin_main']}\n")
    buf_00.write(f"  Commits adelante: {git_be['ahead']}\n")
    buf_00.write(f"  Clasificación: {git_be['clasificacion']}\n")
    if git_be["status_porcelain"]:
        buf_00.write(f"  Archivos sin comitear:\n{git_be['status_porcelain']}\n")

    buf_00.write(f"\nFrontend (argentum-frontend):\n")
    buf_00.write(f"  HEAD: {git_fe['head']}\n")
    buf_00.write(f"  origin/main: {git_fe['origin_main']}\n")
    buf_00.write(f"  Commits adelante: {git_fe['ahead']}\n")
    buf_00.write(f"  Clasificación: {git_fe['clasificacion']}\n")
    if git_fe["status_porcelain"]:
        buf_00.write(f"  Archivos sin comitear:\n{git_fe['status_porcelain']}\n")

    # 2. Refresco local
    buf_00.write("\n--- 2. REFRESCO BASE LOCAL ---\n")
    print("Ejecutando refresco de base local...")
    cmd_ref = [str(PYTHON_EXE), str(REFRESCAR_PY)]
    out_ref, err_ref, rc_ref = run_cmd(cmd_ref, BACKEND_DIR)
    buf_00.write(out_ref + "\n")
    if err_ref:
        buf_00.write("STDERR:\n" + err_ref + "\n")

    if rc_ref != 0 or "Total tablas: 38 | Tablas iguales: 38 | Tablas distintas: 0" not in out_ref:
        print("CUÁNDO FRENAR: El refresco local no dio 38/38 tablas iguales. Abortando.")
        buf_00.write("\nERROR CRÍTICO: Refresco local fallido (no dio 38/38).\n")
        (raw_dir / f"{paso}_00_inicio.txt").write_text(buf_00.getvalue(), encoding="utf-8")
        return 1
    buf_00.write("OK: Refresco local exitoso (38/38 tablas iguales).\n")

    # 3. Canario
    buf_00.write("\n--- 3. CANARIO DE USUARIOS ---\n")
    canario_ok, msg_canario = verificar_canario_bd()
    buf_00.write(msg_canario + "\n")
    if not canario_ok:
        print(f"CUÁNDO FRENAR: Canario fallido: {msg_canario}")
        buf_00.write("ERROR CRÍTICO: Canario fallido. Abortando.\n")
        (raw_dir / f"{paso}_00_inicio.txt").write_text(buf_00.getvalue(), encoding="utf-8")
        return 1
    buf_00.write("OK: Canario verificado con éxito.\n")

    # Guardar estado_inicio.json
    estado_inicio = {
        "paso": paso,
        "fecha_argentina": fecha_ar,
        "fecha_hora": fecha_hora_ar,
        "backend_head": str(git_be["head"]),
        "frontend_head": str(git_fe["head"]),
        "backend": git_be,
        "frontend": git_fe,
    }
    (raw_dir / "estado_inicio.json").write_text(json.dumps(estado_inicio, indent=2), encoding="utf-8")
    (raw_dir / f"{paso}_00_inicio.txt").write_text(buf_00.getvalue(), encoding="utf-8")
    print(f"Guardado crudo inicio: {paso}_00_inicio.txt")

    # 4. Copia "antes"
    print("Creando copia de respaldo 'antes'...")
    realizar_copia_segura(BACKEND_DIR, backup_antes_dir / "backend", ["app", "scripts", "tests"])
    realizar_copia_segura(FRONTEND_DIR, backup_antes_dir / "frontend", ["src"])

    # 5. Ejecución de herramientas 'antes'
    print("Ejecutando herramientas base para 'antes'...")
    buf_01 = io.StringIO()
    buf_01.write(header)
    buf_01.write(f"=== EJECUCIÓN DE HERRAMIENTAS INICIALES (ANTES: {paso}) ===\n\n")

    # Foto
    print("Tomando foto del motor...")
    cmd_foto = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(FOTO_MOTOR_PY), "--etiqueta", f"antes_{paso}"]
    out_f, err_f, rc_f = run_cmd(cmd_foto)
    buf_01.write("--- FOTO DEL MOTOR (antes) ---\n" + out_f + "\n")
    if err_f:
        buf_01.write("STDERR foto:\n" + err_f + "\n")

    # Personas
    print("Evaluando personas...")
    cmd_per = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(EVALUAR_PERSONAS_PY), "--etiqueta", f"antes_{paso}"]
    out_p, err_p, rc_p = run_cmd(cmd_per)
    buf_01.write("\n--- EVALUAR PERSONAS (antes) ---\n" + out_p + "\n")
    if err_p:
        buf_01.write("STDERR personas:\n" + err_p + "\n")

    # Verificador
    print("Ejecutando verificador testingadmin...")
    cmd_ver = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(VERIFICAR_TESTINGADMIN_PY)]
    out_v, err_v, rc_v = run_cmd(cmd_ver)
    buf_01.write("\n--- VERIFICADOR TESTINGADMIN (antes) ---\n" + out_v + "\n")
    if err_v:
        buf_01.write("STDERR verificador:\n" + err_v + "\n")

    # Suite con volcado de salidas
    print("Ejecutando suite regresión WhatsApp (--forzar-grabadas --volcar-salidas)...")
    salidas_antes_path = raw_dir / "salidas_antes.json"
    cmd_suite = [
        str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(SUITE_PY),
        "--forzar-grabadas", "--volcar-salidas", str(salidas_antes_path),
    ]
    out_s, err_s, rc_s = run_cmd(cmd_suite)
    buf_01.write("\n--- SUITE REGRESIÓN WHATSAPP (antes) ---\n" + out_s + "\n")
    if err_s:
        buf_01.write("STDERR suite:\n" + err_s + "\n")

    (raw_dir / f"{paso}_01_antes.txt").write_text(buf_01.getvalue(), encoding="utf-8")
    print(f"Guardado crudo antes: {paso}_01_antes.txt")
    print(f"Inicio completado con éxito para el paso: {paso}")
    return 0


def ejecutar_cierre(paso: str) -> int:
    raw_dir = RAW_DIR_BASE / paso
    backup_antes_dir = BACKUPS_DIR_BASE / paso / "antes"
    backup_desp_dir = BACKUPS_DIR_BASE / paso / "despues"
    header = generar_encabezado_crudo()

    print(f"=== INICIANDO CIERRE DE PASO: {paso} ===")

    # =========================================================================
    # 1. <paso>_02_cambios.txt
    # =========================================================================
    buf_02 = io.StringIO()
    buf_02.write(header)
    buf_02.write(f"=== 1. ANÁLISIS DE CAMBIOS: {paso} ===\n\n")

    # Escanear archivos antes y actuales
    arch_antes_be = escanear_archivos_monitoreados(backup_antes_dir / "backend", ["app", "scripts", "tests"])
    arch_desp_be = escanear_archivos_monitoreados(BACKEND_DIR, ["app", "scripts", "tests"])

    arch_antes_fe = escanear_archivos_monitoreados(backup_antes_dir / "frontend", ["src"])
    arch_desp_fe = escanear_archivos_monitoreados(FRONTEND_DIR, ["src"])

    arch_antes = {**arch_antes_be, **arch_antes_fe}
    arch_desp = {**arch_desp_be, **arch_desp_fe}

    todos_los_archivos = sorted(list(set(arch_antes.keys()) | set(arch_desp.keys())))

    agregados = []
    modificados = []
    borrados = []
    lineas_por_archivo_antes = {}
    lineas_por_archivo_desp = {}
    cambios_ast_por_archivo: dict[str, list[tuple[str, str]]] = {}

    pyflakes_mensajes_nuevos = []
    pyflakes_mensajes_undefined = []

    for rel_path in todos_los_archivos:
        en_antes = rel_path in arch_antes
        en_desp = rel_path in arch_desp

        code_antes = arch_antes[rel_path].read_text(encoding="utf-8", errors="replace") if en_antes else None
        code_desp = arch_desp[rel_path].read_text(encoding="utf-8", errors="replace") if en_desp else None

        if en_antes:
            lineas_por_archivo_antes[rel_path] = len(code_antes.splitlines())
        if en_desp:
            lineas_por_archivo_desp[rel_path] = len(code_desp.splitlines())

        if not en_antes and en_desp:
            agregados.append(rel_path)
            if rel_path.endswith(".py"):
                ast_diff = comparar_ast_archivo(None, code_desp)
                cambios_ast_por_archivo[rel_path] = list(ast_diff.items())
                nuevos_fl, undef_fl = comparar_mensajes_pyflakes(None, code_desp, filename=rel_path)
                pyflakes_mensajes_nuevos.extend([f"{rel_path}: {m}" for m in nuevos_fl])
                pyflakes_mensajes_undefined.extend([f"{rel_path}: {m}" for m in undef_fl])
            else:
                cambios_ast_por_archivo[rel_path] = [("*", "NUEVA")]
        elif en_antes and not en_desp:
            borrados.append(rel_path)
            if rel_path.endswith(".py"):
                ast_diff = comparar_ast_archivo(code_antes, None)
                cambios_ast_por_archivo[rel_path] = list(ast_diff.items())
            else:
                cambios_ast_por_archivo[rel_path] = [("*", "BORRADA")]
        else:
            # En ambos
            if code_antes != code_desp:
                modificados.append(rel_path)
                if rel_path.endswith(".py"):
                    ast_diff = comparar_ast_archivo(code_antes, code_desp)
                    # Guardamos todos los elementos con su estado
                    cambios_ast_por_archivo[rel_path] = list(ast_diff.items())
                    nuevos_fl, undef_fl = comparar_mensajes_pyflakes(code_antes, code_desp, filename=rel_path)
                    pyflakes_mensajes_nuevos.extend([f"{rel_path}: {m}" for m in nuevos_fl])
                    pyflakes_mensajes_undefined.extend([f"{rel_path}: {m}" for m in undef_fl])
                else:
                    cambios_ast_por_archivo[rel_path] = [("*", "CAMBIADA")]

    buf_02.write(f"Archivos modificados ({len(modificados)}):\n")
    for f in modificados:
        buf_02.write(f"  [MODIFICADO] {f}\n")
    buf_02.write(f"\nArchivos agregados ({len(agregados)}):\n")
    for f in agregados:
        buf_02.write(f"  [AGREGADO]   {f}\n")
    buf_02.write(f"\nArchivos borrados ({len(borrados)}):\n")
    for f in borrados:
        buf_02.write(f"  [BORRADO]    {f}\n")

    buf_02.write("\n--- DETALLE DE FUNCIONES Y NIVEL MÓDULO (AST) ---\n")
    for rel_path, items in cambios_ast_por_archivo.items():
        if rel_path.endswith(".py"):
            buf_02.write(f"\nArchivo: {rel_path}\n")
            for elem, st in items:
                buf_02.write(f"  [{st}] {elem}\n")

    # Control contra permitidas.txt
    buf_02.write("\n--- CONTROL CONTRA permitidas.txt ---\n")
    permitidas_file = raw_dir / "permitidas.txt"
    if not permitidas_file.exists():
        permitidas_ok = False
        fuera_de_lista = ["FALLO: permitidas.txt no existe"]
        buf_02.write("FALLO: permitidas.txt no existe en auditorias/raw/" + paso + "/\n")
    else:
        permitidas_text = permitidas_file.read_text(encoding="utf-8")
        permitidas_ok, fuera_de_lista = verificar_permitidas(cambios_ast_por_archivo, permitidas_text)
        if permitidas_ok:
            buf_02.write("OK: Todos los cambios detectados están cubiertos por permitidas.txt (0 FUERA DE LISTA).\n")
        else:
            buf_02.write(f"FUERA DE LISTA ({len(fuera_de_lista)} elementos no permitidos):\n")
            for fdl in fuera_de_lista:
                buf_02.write(f"  * {fdl}\n")

    # pyflakes
    buf_02.write("\n--- CONTROL PYFLAKES ---\n")
    if pyflakes_mensajes_undefined:
        buf_02.write(f"FALLO: Mensajes nuevos de nombres sin definir ({len(pyflakes_mensajes_undefined)}):\n")
        for u in pyflakes_mensajes_undefined:
            buf_02.write(f"  [FALLO] {u}\n")
    elif pyflakes_mensajes_nuevos:
        buf_02.write(f"Aviso: Mensajes nuevos informados por pyflakes ({len(pyflakes_mensajes_nuevos)}):\n")
        for m in pyflakes_mensajes_nuevos:
            buf_02.write(f"  [INFO] {m}\n")
    else:
        buf_02.write("OK: pyflakes 0 mensajes nuevos.\n")

    # Líneas de cada archivo tocado
    buf_02.write("\n--- CONTEO DE LÍNEAS DE ARCHIVOS TOCADOS (MÁXIMO 1.300) ---\n")
    archivos_tocados = set(modificados) | set(agregados)
    lineas_tocadas_desp = {f: lineas_por_archivo_desp.get(f, 0) for f in archivos_tocados}
    tamanio_ok, excedidos_1300 = verificar_tamanio_lineas(lineas_tocadas_desp, max_lineas=1300)
    for f in sorted(archivos_tocados):
        l_antes = lineas_por_archivo_antes.get(f, "(no existía)")
        l_desp = lineas_por_archivo_desp.get(f, 0)
        st_tam = "EXCEDE 1300 (FALLO)" if l_desp > 1300 else "OK"
        buf_02.write(f"  {f}: antes={l_antes}, después={l_desp} [{st_tam}]\n")

    # git diff completo contra HEAD y untracked
    buf_02.write("\n--- GIT DIFF COMPLETO CONTRA HEAD Y NUEVOS SIN SEGUIMIENTO ---\n")
    buf_02.write("\n[BACKEND DIFF]:\n")
    diff_be, _, _ = run_cmd(["git", "diff", "HEAD"], cwd=BACKEND_DIR)
    buf_02.write(diff_be + "\n" if diff_be else "(sin diff contra HEAD)\n")

    st_be, _, _ = run_cmd(["git", "status", "--porcelain"], cwd=BACKEND_DIR)
    for line in st_be.splitlines():
        if line.startswith("??"):
            untr_file = line[2:].strip()
            p_untr = BACKEND_DIR / untr_file
            if p_untr.is_file():
                buf_02.write(f"\n[BACKEND NUEVO SIN SEGUIMIENTO: {untr_file}]\n")
                buf_02.write(p_untr.read_text(encoding="utf-8", errors="replace") + "\n")

    buf_02.write("\n[FRONTEND DIFF]:\n")
    diff_fe, _, _ = run_cmd(["git", "diff", "HEAD"], cwd=FRONTEND_DIR)
    buf_02.write(diff_fe + "\n" if diff_fe else "(sin diff contra HEAD)\n")

    st_fe, _, _ = run_cmd(["git", "status", "--porcelain"], cwd=FRONTEND_DIR)
    for line in st_fe.splitlines():
        if line.startswith("??"):
            untr_file = line[2:].strip()
            p_untr = FRONTEND_DIR / untr_file
            if p_untr.is_file():
                buf_02.write(f"\n[FRONTEND NUEVO SIN SEGUIMIENTO: {untr_file}]\n")
                buf_02.write(p_untr.read_text(encoding="utf-8", errors="replace") + "\n")

    # Copia "despues"
    print("Creando copia de respaldo 'despues'...")
    realizar_copia_segura(BACKEND_DIR, backup_desp_dir / "backend", ["app", "scripts", "tests"])
    realizar_copia_segura(FRONTEND_DIR, backup_desp_dir / "frontend", ["src"])

    (raw_dir / f"{paso}_02_cambios.txt").write_text(buf_02.getvalue(), encoding="utf-8")
    print(f"Guardado crudo cambios: {paso}_02_cambios.txt")

    # =========================================================================
    # 2. <paso>_03_despues.txt
    # =========================================================================
    buf_03 = io.StringIO()
    buf_03.write(header)
    buf_03.write(f"=== 2. EJECUCIÓN Y COMPARACIÓN 'DESPUÉS': {paso} ===\n\n")

    # Verificar fecha de Argentina
    estado_ini_file = raw_dir / "estado_inicio.json"
    fecha_ini = ""
    if estado_ini_file.exists():
        try:
            data_ini = json.loads(estado_ini_file.read_text(encoding="utf-8"))
            fecha_ini = data_ini.get("fecha_argentina", "")
        except Exception:
            pass

    fecha_cierre_ar, fecha_cierre_hora_ar = obtener_fecha_hora_argentina()
    if not verificar_misma_fecha(fecha_ini, fecha_cierre_ar):
        buf_03.write(f"FECHA DISTINTA: Inicio={fecha_ini}, Cierre={fecha_cierre_ar}. No se realizan comparaciones.\n")
        (raw_dir / f"{paso}_03_despues.txt").write_text(buf_03.getvalue(), encoding="utf-8")
        print("FECHA DISTINTA: El cierre cae en otra fecha de Argentina que el inicio.")
        return 1

    buf_03.write(f"Fecha de Argentina verificada (misma del inicio): {fecha_cierre_ar}\n\n")

    # Foto despues
    print("Tomando foto del motor (después)...")
    cmd_foto_d = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(FOTO_MOTOR_PY), "--etiqueta", f"despues_{paso}"]
    out_fd, err_fd, rc_fd = run_cmd(cmd_foto_d)
    buf_03.write("--- FOTO DEL MOTOR (después) ---\n" + out_fd + "\n")
    if err_fd:
        buf_03.write("STDERR foto después:\n" + err_fd + "\n")

    # Personas despues
    print("Evaluando personas (después)...")
    cmd_per_d = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(EVALUAR_PERSONAS_PY), "--etiqueta", f"despues_{paso}"]
    out_pd, err_pd, rc_pd = run_cmd(cmd_per_d)
    buf_03.write("\n--- EVALUAR PERSONAS (después) ---\n" + out_pd + "\n")
    if err_pd:
        buf_03.write("STDERR personas después:\n" + err_pd + "\n")

    # Verificador despues
    print("Ejecutando verificador testingadmin (después)...")
    cmd_ver_d = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(VERIFICAR_TESTINGADMIN_PY)]
    out_vd, err_vd, rc_vd = run_cmd(cmd_ver_d)
    buf_03.write("\n--- VERIFICADOR TESTINGADMIN (después) ---\n" + out_vd + "\n")
    if err_vd:
        buf_03.write("STDERR verificador después:\n" + err_vd + "\n")

    # Suite despues
    print("Ejecutando suite WhatsApp (después)...")
    salidas_desp_path = raw_dir / "salidas_despues.json"
    cmd_suite_d = [
        str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(SUITE_PY),
        "--forzar-grabadas", "--volcar-salidas", str(salidas_desp_path),
    ]
    out_sd, err_sd, rc_sd = run_cmd(cmd_suite_d)
    buf_03.write("\n--- SUITE REGRESIÓN WHATSAPP (después) ---\n" + out_sd + "\n")
    if err_sd:
        buf_03.write("STDERR suite después:\n" + err_sd + "\n")

    # Comparaciones
    buf_03.write("\n--- COMPARACIONES (ANTES vs DESPUÉS) ---\n")

    # Comparar fotos
    fotos_antes = sorted(FOTOS_DIR.glob(f"foto_*antes_{paso}.json"))
    fotos_desp = sorted(FOTOS_DIR.glob(f"foto_*despues_{paso}.json"))
    out_cf = ""
    rc_cf = 0
    if fotos_antes and fotos_desp:
        cmd_cf = [str(PYTHON_EXE), str(COMPARAR_FOTOS_PY), str(fotos_antes[-1]), str(fotos_desp[-1]), "--ignorar-metadatos"]
        out_cf, _, rc_cf = run_cmd(cmd_cf)
        buf_03.write(out_cf + "\n")
    else:
        buf_03.write(f"AVISO: No se encontraron fotos para comparar antes_{paso} y despues_{paso}\n")

    # Comparar personas
    personas_antes_file = FOTOS_DIR / f"personas_antes_{paso}.json"
    personas_desp_file = FOTOS_DIR / f"personas_despues_{paso}.json"
    personas_iguales = False
    if personas_antes_file.exists() and personas_desp_file.exists():
        data_pa = json.loads(personas_antes_file.read_text(encoding="utf-8"))
        data_pd = json.loads(personas_desp_file.read_text(encoding="utf-8"))
        personas_iguales = (data_pa == data_pd)
        buf_03.write(f"\nPersonas comparacion: {'IGUAL' if personas_iguales else 'DISTINTAS'}\n")
    else:
        buf_03.write("\nAVISO: No se encontraron archivos de personas para comparar.\n")

    # Comparar salidas de suite
    out_cs = ""
    rc_cs = 0
    salidas_antes_path = raw_dir / "salidas_antes.json"
    if salidas_antes_path.exists() and salidas_desp_path.exists():
        cmd_cs = [str(PYTHON_EXE), str(COMPARAR_SALIDAS_PY), str(salidas_antes_path), str(salidas_desp_path)]
        out_cs, _, rc_cs = run_cmd(cmd_cs)
        buf_03.write("\n" + out_cs + "\n")
    else:
        buf_03.write("\nAVISO: No se encontraron archivos de salidas para comparar_salidas.\n")

    # Build frontend si cambió algo de frontend
    cambio_frontend = any(f.startswith("src/") for f in archivos_tocados)
    out_build = ""
    rc_build = 0
    build_ejecutado = False
    build_fallo_en_tocados = False
    if cambio_frontend:
        build_ejecutado = True
        print("Cambió frontend: ejecutando npm run build...")
        out_build, err_build, rc_build = run_cmd("npm run build", cwd=FRONTEND_DIR, shell=True)
        buf_03.write("\n--- FRONTEND BUILD (npm run build) ---\n" + out_build + "\n")
        if err_build:
            buf_03.write("STDERR build:\n" + err_build + "\n")
        if rc_build != 0:
            # Inspeccionar si el error está en archivos tocados
            for f in archivos_tocados:
                if f.startswith("src/") and Path(f).name in (out_build + err_build):
                    build_fallo_en_tocados = True
                    break
    else:
        buf_03.write("\n--- FRONTEND BUILD: No ejecutado (no hubo cambios en src/) ---\n")

    (raw_dir / f"{paso}_03_despues.txt").write_text(buf_03.getvalue(), encoding="utf-8")
    print(f"Guardado crudo despues: {paso}_03_despues.txt")

    # =========================================================================
    # 3. <paso>_04_verificar.txt
    # =========================================================================
    print("Ejecutando verificaciones integrales (pytest y verificar_todo)...")
    buf_04 = io.StringIO()
    buf_04.write(header)
    buf_04.write(f"=== 3. VERIFICACIONES INTEGRALES: {paso} ===\n\n")

    # Pytest completo
    print("Ejecutando pytest completo...")
    cmd_pytest = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), "-m", "pytest"]
    out_py, err_py, rc_py = run_cmd(cmd_pytest)
    buf_04.write("--- PYTEST COMPLETO ---\n" + out_py + "\n")
    if err_py:
        buf_04.write("STDERR pytest:\n" + err_py + "\n")

    # verificar_todo.py
    print("Ejecutando verificar_todo.py...")
    cmd_vt = [str(PYTHON_EXE), str(CON_BASE_LOCAL_PY), str(PYTHON_EXE), str(VERIFICAR_TODO_PY)]
    out_vt, err_vt, rc_vt = run_cmd(cmd_vt)
    buf_04.write("\n--- VERIFICAR TODO ---\n" + out_vt + "\n")
    if err_vt:
        buf_04.write("STDERR verificar_todo:\n" + err_vt + "\n")

    (raw_dir / f"{paso}_04_verificar.txt").write_text(buf_04.getvalue(), encoding="utf-8")
    print(f"Guardado crudo verificar: {paso}_04_verificar.txt")

    # =========================================================================
    # 4. <paso>_05_resumen.txt
    # =========================================================================
    print("Armando resumen final...")
    buf_05 = io.StringIO()
    buf_05.write(header)
    buf_05.write(f"=== 4. RESUMEN FINAL: {paso} ===\n\n")

    # Controles OK o FALLO
    control_git = "OK"  # Git se registra y sigue
    control_fecha = "OK" if verificar_misma_fecha(fecha_ini, fecha_cierre_ar) else "FALLO"
    control_permitidas = "OK" if permitidas_ok else "FALLO"
    control_pyflakes = "FALLO" if pyflakes_mensajes_undefined else "OK"
    control_tamanio = "OK" if tamanio_ok else "FALLO"
    control_fotos = "OK" if rc_cf == 0 else "FALLO"
    control_personas = "OK" if personas_iguales else "FALLO"
    control_salidas = "OK" if rc_cs == 0 else "FALLO"
    control_verificador = "OK" if rc_vd == 0 else "FALLO"
    control_suite = "OK" if rc_sd == 0 else "FALLO"
    control_pytest = "OK" if rc_py == 0 else "FALLO"
    control_verificar_todo = "OK" if rc_vt == 0 else "FALLO"

    if build_ejecutado:
        control_build = "FALLO" if build_fallo_en_tocados else "OK"
    else:
        control_build = "OMITIDO (sin cambios frontend)"

    buf_05.write(f"[CONTROL] Fecha de Argentina: {control_fecha}\n")
    buf_05.write(f"[CONTROL] permitidas.txt y elementos fuera de lista: {control_permitidas}\n")
    buf_05.write(f"[CONTROL] pyflakes (nombres sin definir): {control_pyflakes}\n")
    buf_05.write(f"[CONTROL] Tamaño de archivos (<= 1.300 líneas): {control_tamanio}\n")
    buf_05.write(f"[CONTROL] Foto del motor (comparar_fotos): {control_fotos}\n")
    buf_05.write(f"[CONTROL] Personas (comparación sintética): {control_personas}\n")
    buf_05.write(f"[CONTROL] Salidas suite (comparar_salidas): {control_salidas}\n")
    buf_05.write(f"[CONTROL] Verificador testingadmin: {control_verificador}\n")
    buf_05.write(f"[CONTROL] Suite regresión WhatsApp: {control_suite}\n")
    buf_05.write(f"[CONTROL] Pytest completo: {control_pytest}\n")
    buf_05.write(f"[CONTROL] Verificar todo: {control_verificar_todo}\n")
    buf_05.write(f"[CONTROL] Frontend build: {control_build}\n\n")

    # Líneas finales textuales de cada herramienta
    buf_05.write("--- LÍNEAS FINALES TEXTUALES DE CADA HERRAMIENTA ---\n")

    def _ultima_linea(txt: str) -> str:
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        for l in reversed(lines):
            if set(l) - set("=- "):
                return l
        return lines[-1] if lines else "(sin salida)"

    # Pytest
    py_last = [l for l in out_py.splitlines() if "=" in l and ("passed" in l or "failed" in l)]
    buf_05.write(f"Pytest: {py_last[-1].strip() if py_last else _ultima_linea(out_py)}\n")

    # Suite (Total y Llamadas IA)
    suite_tot = [l for l in out_sd.splitlines() if "Total:" in l and "Aprobados:" in l]
    suite_ia = [l for l in out_sd.splitlines() if "Llamadas IA:" in l]
    buf_05.write(f"Suite (Total): {suite_tot[-1].strip() if suite_tot else '(no encontrada)'}\n")
    buf_05.write(f"Suite (Llamadas IA): {suite_ia[-1].strip() if suite_ia else '(no encontrada)'}\n")

    # Comparar salidas
    cs_tot = [l for l in out_cs.splitlines() if "Total escenarios:" in l]
    buf_05.write(f"Comparar salidas: {cs_tot[-1].strip() if cs_tot else _ultima_linea(out_cs)}\n")

    # Fotos
    cf_res = [l for l in out_cf.splitlines() if "RESULTADO:" in l]
    buf_05.write(f"Fotos: {cf_res[-1].strip() if cf_res else _ultima_linea(out_cf)}\n")

    # Personas
    buf_05.write(f"Personas: Personas comparacion: {'IGUAL' if personas_iguales else 'DISTINTAS'}\n")

    # Verificador
    ver_res = [l for l in out_vd.splitlines() if "ESTADO:" in l or "VERIFICACION" in l]
    buf_05.write(f"Verificador: {ver_res[-1].strip() if ver_res else _ultima_linea(out_vd)}\n")

    # Verificar todo
    buf_05.write(f"Verificar todo: {_ultima_linea(out_vt)}\n")

    # Build
    if build_ejecutado:
        buf_05.write(f"Build: {_ultima_linea(out_build)}\n")
    else:
        buf_05.write("Build: (No ejecutado: frontend no modificado)\n")

    # Contadores
    buf_05.write(f"\nCantidad de elementos FUERA DE LISTA: {len(fuera_de_lista)}\n")
    buf_05.write(f"Cantidad de mensajes nuevos de pyflakes: {len(pyflakes_mensajes_nuevos)}\n")

    # Archivos de más de 1.300 líneas
    if excedidos_1300:
        buf_05.write(f"Archivos de más de 1.300 líneas: {excedidos_1300}\n")
    else:
        buf_05.write("Archivos de más de 1.300 líneas: Ninguno\n")

    # Líneas con FALLO, DESVIO, ERROR o INESPERADA nuevas en cierre vs inicio
    texto_inicio_total = (raw_dir / f"{paso}_01_antes.txt").read_text(encoding="utf-8") if (raw_dir / f"{paso}_01_antes.txt").exists() else ""
    texto_cierre_total = buf_03.getvalue() + "\n" + buf_04.getvalue()
    alertas_nuevas = comparar_lineas_alerta(texto_inicio_total, texto_cierre_total)
    buf_05.write(f"\nLíneas de alerta nuevas en cierre ({len(alertas_nuevas)}):\n")
    if alertas_nuevas:
        for a in alertas_nuevas[:50]:
            buf_05.write(f"  - {a}\n")
    else:
        buf_05.write("  Ninguna\n")

    # Última línea obligatoria
    cambio_app = any(f.startswith("app/") for f in archivos_tocados)
    if rc_vt == 0 and rc_sd == 0:
        ultima_linea = "NO CORRAS LA SUITE"
    elif cambio_app and rc_sd != 0:
        ultima_linea = "CORRE LA SUITE"
    else:
        ultima_linea = "NO CORRAS LA SUITE" if (rc_vt == 0 and rc_sd == 0) else "CORRE LA SUITE"

    buf_05.write(f"\n{ultima_linea}\n")

    resumen_content = buf_05.getvalue()
    (raw_dir / f"{paso}_05_resumen.txt").write_text(resumen_content, encoding="utf-8")
    print(f"Guardado crudo resumen: {paso}_05_resumen.txt")
    print("\n" + resumen_content)
    return 0


def main():
    if len(sys.argv) < 3:
        print("Uso:")
        print("  python paso.py inicio <paso>")
        print("  python paso.py cierre <paso>")
        sys.exit(1)

    subcomando = sys.argv[1].lower()
    paso = sys.argv[2].strip()

    if subcomando == "inicio":
        sys.exit(ejecutar_inicio(paso))
    elif subcomando == "cierre":
        sys.exit(ejecutar_cierre(paso))
    else:
        print(f"Comando desconocido: {subcomando}. Use 'inicio' o 'cierre'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
