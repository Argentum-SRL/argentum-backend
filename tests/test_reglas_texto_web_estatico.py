"""Auditoría estática AST de textos web de la campanita (DW.8.3)."""
from __future__ import annotations

import ast
from pathlib import Path

from app.core.reglas_texto_web import problemas_texto_web

RAIZ = Path(__file__).resolve().parents[1]
RUTA_APP = RAIZ / "app"
RUTA_TEXTOS_WEB = RAIZ / "app" / "services" / "textos_avisos_web.py"
RUTA_MOD_NOTIF = RAIZ / "app" / "models" / "notificacion.py"


def _nombres_funciones_textos_web() -> set[str]:
    mod = ast.parse(RUTA_TEXTOS_WEB.read_text(encoding="utf-8"))
    return {
        n.name
        for n in mod.body
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")
    }


def _constantes_mensaje_notif() -> set[str]:
    mod = ast.parse(RUTA_MOD_NOTIF.read_text(encoding="utf-8"))
    res: set[str] = set()
    for st in mod.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name) and t.id.startswith("MENSAJE_"):
                    res.add(t.id)
    return res


def _es_expresion_permitida(
    nodo: ast.AST,
    fns_web: set[str],
    consts_notif: set[str],
    vars_ok: set[str],
) -> bool:
    if isinstance(nodo, ast.Name):
        return nodo.id in consts_notif or nodo.id in vars_ok
    if isinstance(nodo, ast.Call):
        fn = nodo.func
        nombre = (
            fn.attr
            if isinstance(fn, ast.Attribute)
            else (fn.id if isinstance(fn, ast.Name) else None)
        )
        return nombre in fns_web or nombre == "texto_aviso_fin_de_mes"
    return False


def _extraer_mensaje_call(call: ast.Call) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == "mensaje":
            return kw.value
    if len(call.args) >= 5:
        return call.args[4]
    return None


def _funciones_productoras_tuplas(
    mod: ast.Module,
    fns_web: set[str],
    consts_notif: set[str],
) -> dict[str, set[int]]:
    """Detecta funciones del módulo que devuelven listas de tuplas con textos permitidos."""
    res: dict[str, set[int]] = {}
    for node in mod.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        vars_ok: set[str] = set()
        indices_ok: set[int] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name) and _es_expresion_permitida(
                        sub.value, fns_web, consts_notif, vars_ok
                    ):
                        vars_ok.add(t.id)
            elif (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "append"
                and len(sub.args) == 1
                and isinstance(sub.args[0], ast.Tuple)
            ):
                for idx, el in enumerate(sub.args[0].elts):
                    if _es_expresion_permitida(el, fns_web, consts_notif, vars_ok):
                        indices_ok.add(idx)
        if indices_ok:
            res[node.name] = indices_ok
    return res


def _revisar_funcion_calls(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    rel: str,
    fns_web: set[str],
    consts_notif: set[str],
    prod_tuplas: dict[str, set[int]],
) -> tuple[int, list[str]]:
    vars_ok: set[str] = set()
    vars_bad: set[str] = set()
    vars_tuplas: dict[str, set[int]] = {}
    llamadas = 0
    problemas: list[str] = []

    for sub in ast.walk(fn_node):
        if isinstance(sub, ast.Assign):
            for t in sub.targets:
                if isinstance(t, ast.Name):
                    if isinstance(sub.value, ast.Call):
                        fn = sub.value.func
                        nom = (
                            fn.attr
                            if isinstance(fn, ast.Attribute)
                            else (fn.id if isinstance(fn, ast.Name) else None)
                        )
                        if nom in prod_tuplas:
                            vars_tuplas[t.id] = prod_tuplas[nom]
                    if _es_expresion_permitida(sub.value, fns_web, consts_notif, vars_ok):
                        vars_ok.add(t.id)
                        vars_bad.discard(t.id)
                    else:
                        vars_bad.add(t.id)
        elif isinstance(sub, ast.For):
            if (
                isinstance(sub.iter, ast.Name)
                and sub.iter.id in vars_tuplas
                and isinstance(sub.target, ast.Tuple)
            ):
                idxs = vars_tuplas[sub.iter.id]
                for idx, el in enumerate(sub.target.elts):
                    if idx in idxs and isinstance(el, ast.Name):
                        vars_ok.add(el.id)
                        vars_bad.discard(el.id)
        elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
            if sub.value is not None:
                if _es_expresion_permitida(sub.value, fns_web, consts_notif, vars_ok):
                    vars_ok.add(sub.target.id)
                    vars_bad.discard(sub.target.id)
                else:
                    vars_bad.add(sub.target.id)

    for sub in ast.walk(fn_node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        name = (
            fn.attr
            if isinstance(fn, ast.Attribute)
            else (fn.id if isinstance(fn, ast.Name) else None)
        )
        if name != "crear_notificacion":
            continue
        llamadas += 1
        msg_node = _extraer_mensaje_call(sub)
        if msg_node is None:
            problemas.append(f"{rel}:{sub.lineno} sin argumento mensaje")
            continue
        if isinstance(msg_node, ast.Name) and msg_node.id in vars_bad:
            problemas.append(
                f"{rel}:{sub.lineno} variable '{msg_node.id}' no viene de textos_avisos_web"
            )
            continue
        if not _es_expresion_permitida(msg_node, fns_web, consts_notif, vars_ok):
            problemas.append(
                f"{rel}:{sub.lineno} mensaje no usa textos_avisos_web ni MENSAJE_*"
            )

    return llamadas, problemas


def revisar_fuente_crear_notificacion(
    fuente: str,
    rel: str = "<memoria>",
    fns_web: set[str] | None = None,
    consts_notif: set[str] | None = None,
) -> tuple[int, list[str]]:
    """Audita llamadas a crear_notificacion en un código fuente Python."""
    if fns_web is None:
        fns_web = _nombres_funciones_textos_web()
    if consts_notif is None:
        consts_notif = _constantes_mensaje_notif()
    mod = ast.parse(fuente, filename=rel)
    prod_tuplas = _funciones_productoras_tuplas(mod, fns_web, consts_notif)
    total_llamadas = 0
    problemas: list[str] = []

    for node in ast.walk(mod):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            n_calls, probs = _revisar_funcion_calls(
                node, rel, fns_web, consts_notif, prod_tuplas
            )
            total_llamadas += n_calls
            problemas.extend(probs)
    return total_llamadas, problemas


def _render_nodo(nodo: ast.AST) -> list[str]:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return [nodo.value]
    if isinstance(nodo, ast.JoinedStr):
        partes: list[str] = []
        for v in nodo.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                partes.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                partes.append("X")
        return ["".join(partes)]
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
        izqs = _render_nodo(nodo.left)
        ders = _render_nodo(nodo.right)
        if izqs and ders:
            return [i + d for i in izqs for d in ders]
        return izqs or ders
    return []


def _textos_de_funcion_web(fn_node: ast.FunctionDef) -> list[str]:
    textos: list[str] = []
    for nodo in ast.walk(fn_node):
        if isinstance(nodo, ast.Return) and nodo.value is not None:
            textos.extend(_render_nodo(nodo.value))
        elif (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Attribute)
            and nodo.func.attr == "append"
            and len(nodo.args) == 1
        ):
            textos.extend(_render_nodo(nodo.args[0]))
    return [t for t in textos if t.strip()]


def test_tw4_control_interno_estatico():
    ejemplo = (
        "def f(db, uid, x):\n"
        '    crear_notificacion(db, uid, None, None, mensaje=f"Hola {x}")\n'
    )
    llamadas, probs = revisar_fuente_crear_notificacion(ejemplo, "ejemplo.py")
    assert llamadas == 1
    assert len(probs) == 1


def test_reglas_texto_web_estatico():
    fns_web = _nombres_funciones_textos_web()
    consts_notif = _constantes_mensaje_notif()
    total_llamadas = 0
    problemas_calls: list[str] = []

    for ruta in sorted(RUTA_APP.rglob("*.py")):
        rel = ruta.relative_to(RAIZ).as_posix()
        fuente = ruta.read_text(encoding="utf-8")
        if "crear_notificacion" not in fuente:
            continue
        if rel == "app/services/notificacion_service.py":
            continue
        n_calls, probs = revisar_fuente_crear_notificacion(
            fuente, rel, fns_web, consts_notif
        )
        total_llamadas += n_calls
        problemas_calls.extend(probs)

    mod_web = ast.parse(RUTA_TEXTOS_WEB.read_text(encoding="utf-8"))
    fns_revisadas = 0
    total_textos = 0
    problemas_textos: list[str] = []
    for node in mod_web.body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            fns_revisadas += 1
            for txt in _textos_de_funcion_web(node):
                total_textos += 1
                cods = [
                    c for c in problemas_texto_web(txt) if not c.startswith("W8_SIN_RELLENAR")
                ]
                if cods:
                    problemas_textos.append(
                        f"textos_avisos_web.py::{node.name} {cods} -> {txt!r}"
                    )

    print(
        f"\nLlamadas a crear_notificacion revisadas: {total_llamadas}; "
        f"funciones textos_avisos_web: {fns_revisadas}; textos: {total_textos}"
    )
    assert total_llamadas >= 24, f"Se esperaban >= 24 llamadas, hubo {total_llamadas}"
    assert fns_revisadas >= 17, f"Se esperaban >= 17 funciones, hubo {fns_revisadas}"
    assert not problemas_calls, "Llamadas inválidas:\n" + "\n".join(problemas_calls)
    assert not problemas_textos, "Textos inválidos:\n" + "\n".join(problemas_textos)
