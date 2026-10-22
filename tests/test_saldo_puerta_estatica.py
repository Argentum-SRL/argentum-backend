"""
tests/test_saldo_puerta_estatica.py — TD.1 Verificación estática AST de la puerta única de saldos.

Asegura que en app/ fuera de app/services/saldo_billetera_service.py:
- 0 asignaciones (=, +=, -=, anotadas) a saldo_actual, saldo_inicial, fecha_saldo, fecha_ultimo_control.
- 0 setattr con esos nombres.
- 0 .values(...) con esas claves.
- 0 textos con "UPDATE billeteras".
(Los constructores Billetera(...) no cuentan).
"""
import ast
from pathlib import Path


TARGET_FIELDS = {"saldo_actual", "saldo_inicial", "fecha_saldo", "fecha_ultimo_control"}


def buscar_violaciones_ast_en_archivo(filepath: Path) -> list[str]:
    hallazgos = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        return [f"{filepath}:0: Error leyendo archivo: {e}"]

    # Búsqueda textual de UPDATE billeteras
    if "UPDATE billeteras" in content.upper() or "UPDATE BILLETERAS" in content:
        # Detectar líneas exactas
        for idx, line in enumerate(content.splitlines(), start=1):
            if "update billeteras" in line.lower():
                hallazgos.append(f"{filepath}:{idx}: Texto con UPDATE billeteras: '{line.strip()}'")

    try:
        tree = ast.parse(content, filename=str(filepath))
    except Exception as e:
        return [f"{filepath}:0: Error parseando AST: {e}"]

    for node in ast.walk(tree):
        # 1. Asignación directa (=)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in TARGET_FIELDS:
                    hallazgos.append(f"{filepath}:{node.lineno}: Asignación directa a .{target.attr}")

        # 2. Asignación aumentada (+=, -=, etc.)
        elif isinstance(node, ast.AugAssign):
            target = node.target
            if isinstance(target, ast.Attribute) and target.attr in TARGET_FIELDS:
                hallazgos.append(f"{filepath}:{node.lineno}: Asignación aumentada a .{target.attr}")

        # 3. Asignación anotada (x: int = y)
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Attribute) and target.attr in TARGET_FIELDS:
                hallazgos.append(f"{filepath}:{node.lineno}: Asignación anotada a .{target.attr}")

        # 4. Llamadas a función: setattr o .values(...)
        elif isinstance(node, ast.Call):
            # setattr(obj, "attr", val)
            if isinstance(node.func, ast.Name) and node.func.id == "setattr":
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    if node.args[1].value in TARGET_FIELDS:
                        hallazgos.append(f"{filepath}:{node.lineno}: Llamada a setattr con '{node.args[1].value}'")

            # .values(saldo_actual=...)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "values":
                for kw in node.keywords:
                    if kw.arg in TARGET_FIELDS:
                        hallazgos.append(f"{filepath}:{node.lineno}: Llamada a .values() con clave '{kw.arg}'")
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        for k in arg.keys:
                            if isinstance(k, ast.Constant) and k.value in TARGET_FIELDS:
                                hallazgos.append(f"{filepath}:{node.lineno}: Llamada a .values(dict) con clave '{k.value}'")

    return hallazgos


def test_td1_saldo_puerta_estatica():
    app_dir = Path(__file__).resolve().parent.parent / "app"
    archivos_py = sorted(list(app_dir.rglob("*.py")))
    todos_los_hallazgos = []

    for f in archivos_py:
        # Excluir saldo_billetera_service.py (es la puerta única)
        # Y modelos si definen columnas (en app/models/billetera.py las declaraciones son Column(...) o mapped_column)
        if f.name == "saldo_billetera_service.py":
            continue

        hallazgos = buscar_violaciones_ast_en_archivo(f)
        if hallazgos:
            todos_los_hallazgos.extend(hallazgos)

    for h in todos_los_hallazgos:
        print(f"HALLAZGO: {h}")

    assert len(todos_los_hallazgos) == 0, (
        f"Se encontraron {len(todos_los_hallazgos)} escrituras fuera de saldo_billetera_service.py:\n"
        + "\n".join(todos_los_hallazgos)
    )
