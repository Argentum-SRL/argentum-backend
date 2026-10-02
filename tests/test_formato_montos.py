import ast
from pathlib import Path
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BACKEND_DIR / "app"

CURRENCY_PREFIXES = ("$", "US$", "USD ", "U$S ", "$ ", "US$ ", "U$S ")
MONTO_KEYWORDS = (
    "monto", "saldo", "total", "precio", "importe", "valor", "costo",
    "ingreso", "gasto", "balance", "cotiz", "runway", "habitos",
    "comprometido", "presupuesto", "debito", "credito", "disponible",
    "variable_tipico", "gasto_tipico"
)

def check_calls_formato(expr_node):
    """Retorna True si la expresión invoca formatear_monto o _fmt."""
    for n in ast.walk(expr_node):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name) and n.func.id in ("formatear_monto", "_fmt"):
                return True
            if isinstance(n.func, ast.Attribute) and n.func.attr in ("formatear_monto", "_fmt"):
                return True
    return False

def get_format_spec_str(fmt_node):
    if fmt_node is None:
        return ""
    if isinstance(fmt_node, ast.JoinedStr):
        parts = []
        for val in fmt_node.values:
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                parts.append(val.value)
        return "".join(parts)
    return ""

def test_no_referencias_gasto_inusual_en_app():
    """Control permanente: falla si quedan referencias a evaluar_gasto_inusual o _evaluar_gasto_inusual_safe en app/."""
    simbolos_prohibidos = {"evaluar_gasto_inusual", "_evaluar_gasto_inusual_safe"}
    hallazgos = []

    for py_file in APP_DIR.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in simbolos_prohibidos:
                    hallazgos.append(f"{py_file.name}:{node.lineno} (def {node.name})")
            elif isinstance(node, ast.Call):
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in simbolos_prohibidos:
                    hallazgos.append(f"{py_file.name}:{node.lineno} (call {name})")
            elif isinstance(node, ast.Name):
                if node.id in simbolos_prohibidos:
                    hallazgos.append(f"{py_file.name}:{node.lineno} (ref {node.id})")

    assert not hallazgos, f"Se encontraron referencias a gasto inusual en app/: {hallazgos}"


def test_montos_hacia_usuario_usan_formatear_monto():
    """
    Control permanente por AST: falla si en app/ hay una f-string con dinero hacia el usuario
    que no use formatear_monto o _fmt (los logs e internas de hash quedan excluidos).
    """
    class FStringMoneyVisitor(ast.NodeVisitor):
        def __init__(self, filepath, lines):
            self.filepath = filepath
            self.lines = lines
            self.parent_stack = []
            self.violaciones = []

        def visit_FunctionDef(self, node):
            self.parent_stack.append(node)
            self.generic_visit(node)
            self.parent_stack.pop()

        def visit_AsyncFunctionDef(self, node):
            self.parent_stack.append(node)
            self.generic_visit(node)
            self.parent_stack.pop()

        def visit_JoinedStr(self, node):
            raw_text = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
            line_lower = raw_text.lower()

            # Excluir logs
            if any(log_call in line_lower for log_call in ("logger.", "logging.", "print(")):
                self.parent_stack.append(node)
                self.generic_visit(node)
                self.parent_stack.pop()
                return

            # Excluir hashes internos (como persistencia_service calcular_import_hash)
            if "calcular_import_hash" in [getattr(p, 'name', '') for p in self.parent_stack]:
                self.parent_stack.append(node)
                self.generic_visit(node)
                self.parent_stack.pop()
                return

            values = node.values
            for idx, val in enumerate(values):
                # Criterio 1: prefijo de moneda seguido de valor sin formatear_monto
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    s = val.value
                    if any(s.endswith(p) for p in CURRENCY_PREFIXES):
                        if idx + 1 < len(values) and isinstance(values[idx + 1], ast.FormattedValue):
                            # Excluir conteos explícitos de dólares enteros en flujos de diálogo de whatsapp donde ya se maneja número de dólares (ej. USD 100)
                            val_node = values[idx + 1].value
                            expr_str = ast.unparse(val_node)
                            # Si es un monto que no llama a formatear_monto ni _fmt
                            if not check_calls_formato(val_node):
                                # Si no es un conteo/entero de dólares en transferencias (dolares_str, d_str)
                                if expr_str not in ("dolares_str", "d_str", "c1_d_str", "c2_d_str"):
                                    self.violaciones.append(
                                        f"{self.filepath}:{node.lineno} Criterio 1: '{s}' seguido de '{expr_str}'"
                                    )

                # Criterio 2: spec con , o .2f o .0f aplicado a un monto
                if isinstance(val, ast.FormattedValue):
                    spec = get_format_spec_str(val.format_spec)
                    if any(k in spec for k in [",", ".2f", ".0f"]):
                        expr_str = ast.unparse(val.value).lower()
                        es_porcentaje = any(pct in expr_str for pct in ["pct", "porcentaje", "ratio", "tasa"]) or "%" in raw_text
                        if any(mk in expr_str for mk in MONTO_KEYWORDS) and not (es_porcentaje and "monto" not in expr_str):
                            if not check_calls_formato(val.value):
                                self.violaciones.append(
                                    f"{self.filepath}:{node.lineno} Criterio 2: spec '{spec}' en '{expr_str}'"
                                )

            self.parent_stack.append(node)
            self.generic_visit(node)
            self.parent_stack.pop()

    todas_violaciones = []
    for py_file in sorted(APP_DIR.rglob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        lines = content.splitlines()
        try:
            tree = ast.parse(content, filename=str(py_file))
        except Exception:
            continue

        visitor = FStringMoneyVisitor(py_file.name, lines)
        visitor.visit(tree)
        todas_violaciones.extend(visitor.violaciones)

    assert not todas_violaciones, f"F-strings con dinero hacia el usuario sin formatear_monto:\n" + "\n".join(todas_violaciones)
