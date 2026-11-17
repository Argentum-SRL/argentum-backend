"""Auditoría estática AST de textos de WhatsApp en el alcance de DT.4."""
from __future__ import annotations

import ast
from pathlib import Path

from app.core.reglas_texto_wpp import problemas_texto_wpp


RAIZ = Path(__file__).resolve().parents[1]
RUTA_WHATSAPP = RAIZ / "app" / "routers" / "whatsapp"
ARCHIVOS_EXTRA = [
    RAIZ / "app" / "routers" / "whatsapp_ia.py",
    RAIZ / "app" / "services" / "whatsapp_service.py",
    RAIZ / "app" / "services" / "permitirse_textos.py",
    RAIZ / "app" / "services" / "fin_de_mes_textos.py",
    RAIZ / "app" / "models" / "notificacion.py",
]
EXCLUIDOS_WPP = {"extraccion_documento.py"}
FUNCIONES_POR_ARCHIVO = {
    "fin_de_mes_textos.py": {"mensaje_wpp", "linea_cobertura"},
}
CONSTANTES_POR_ARCHIVO = {
    "notificacion.py": {"MENSAJE_CAMBIO_CONTRASENA_WPP"},
}
PREFIJOS_FUNCION = ("texto_", "linea_", "msg_", "mensaje_", "armar_", "formatear_")
PREFIJOS_CONST = ("MSG_", "MENSAJE_", "TEXTO_")
PREFIJOS_VAR = ("msg", "mensaje", "texto", "resp")


def _archivos_alcance() -> list[Path]:
    archivos = sorted(
        p for p in RUTA_WHATSAPP.glob("*.py") if p.name not in EXCLUIDOS_WPP
    )
    archivos.extend(ARCHIVOS_EXTRA)
    return archivos


def _es_logger_o_excepcion(nodo: ast.AST) -> bool:
    if isinstance(nodo, ast.Raise):
        return True
    if isinstance(nodo, ast.Call):
        fn = nodo.func
        if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            if fn.value.id in ("logger", "log"):
                return True
        if isinstance(fn, ast.Name) and (
            fn.id.endswith("Error")
            or fn.id.endswith("Exception")
            or fn.id == "HTTPException"
        ):
            return True
    return False


def _es_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _render_nodo_texto(nodo: ast.AST) -> list[str]:
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return [nodo.value] if nodo.value.strip() else []
    if isinstance(nodo, ast.JoinedStr):
        partes: list[str] = []
        for v in nodo.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                partes.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                partes.append("X")
        txt = "".join(partes)
        return [txt] if txt.strip() else []
    if isinstance(nodo, ast.IfExp):
        return _render_nodo_texto(nodo.body) + _render_nodo_texto(nodo.orelse)
    if isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add):
        izqs = _render_nodo_texto(nodo.left)
        ders = _render_nodo_texto(nodo.right)
        if izqs and ders:
            return [i + d for i in izqs for d in ders]
        return izqs or ders
    if isinstance(nodo, ast.Call):
        fn = nodo.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr == "join"
            and isinstance(fn.value, ast.Constant)
            and isinstance(fn.value.value, str)
            and len(nodo.args) == 1
            and isinstance(nodo.args[0], (ast.List, ast.Tuple))
        ):
            sep = fn.value.value
            elems: list[str] = []
            for el in nodo.args[0].elts:
                rend = _render_nodo_texto(el)
                elems.append(rend[0] if rend else "X")
            unido = sep.join(elems)
            return [unido] if unido.strip() else []
    return []


def _nombre_es_funcion_texto(fn: ast.FunctionDef | ast.AsyncFunctionDef, en_wpp: bool) -> bool:
    nom = fn.name.lstrip("_")
    if nom.startswith(PREFIJOS_FUNCION):
        return True
    if en_wpp and fn.returns is not None:
        if isinstance(fn.returns, ast.Name) and fn.returns.id == "str":
            return True
    return False


def _es_target_respuesta_usuario(target: ast.AST) -> bool:
    return (
        isinstance(target, ast.Subscript)
        and isinstance(target.slice, ast.Constant)
        and target.slice.value == "respuesta_usuario"
    )


def _es_llamada_enviar_wpp(nodo: ast.Call) -> bool:
    fn = nodo.func
    if isinstance(fn, ast.Name) and fn.id == "enviar_whatsapp":
        return True
    if isinstance(fn, ast.Attribute) and fn.attr == "enviar_whatsapp":
        return True
    return False


def _es_texto_para_persona(txt: str) -> bool:
    if not txt or " " not in txt:
        return False
    primero = txt[0]
    return (
        primero.isupper()
        or primero in "¿¡$_"
        or primero.isdigit()
        or txt.startswith("US$")
    )


def _juntar_textos_archivo(ruta: Path) -> list[tuple[int, str]]:
    fuente = ruta.read_text(encoding="utf-8")
    mod = ast.parse(fuente, filename=str(ruta))
    en_wpp = ruta.parent == RUTA_WHATSAPP
    funcs_permitidas = FUNCIONES_POR_ARCHIVO.get(ruta.name)
    consts_permitidas = CONSTANTES_POR_ARCHIVO.get(ruta.name)
    encontrados: list[tuple[int, str]] = []

    def _agregar(linea: int, nodo: ast.AST | None) -> None:
        if nodo is None:
            return
        for txt in _render_nodo_texto(nodo):
            if _es_texto_para_persona(txt):
                encontrados.append((linea, txt))

    def _visitar_stmts(
        stmts: list[ast.stmt],
        es_nivel_mod: bool,
        es_fn_texto: bool,
    ) -> None:
        for i, st in enumerate(stmts):
            if i == 0 and _es_docstring(st):
                continue
            if _es_logger_o_excepcion(st):
                continue
            _visitar_nodo(st, es_nivel_mod, es_fn_texto)

    def _visitar_nodo(
        nodo: ast.AST,
        es_nivel_mod: bool,
        es_fn_texto: bool,
    ) -> None:
        if _es_logger_o_excepcion(nodo):
            return
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if funcs_permitidas is not None and nodo.name not in funcs_permitidas:
                return
            fn_txt = _nombre_es_funcion_texto(nodo, en_wpp)
            _visitar_stmts(nodo.body, False, fn_txt)
            return
        if isinstance(nodo, ast.ClassDef):
            _visitar_stmts(nodo.body, False, False)
            return
        if isinstance(nodo, ast.Return) and es_fn_texto:
            _agregar(nodo.lineno, nodo.value)
        elif isinstance(nodo, ast.Assign):
            for t in nodo.targets:
                if _es_target_respuesta_usuario(t):
                    _agregar(nodo.lineno, nodo.value)
                elif isinstance(t, ast.Name):
                    if es_nivel_mod and t.id.startswith(PREFIJOS_CONST):
                        if consts_permitidas is None or t.id in consts_permitidas:
                            _agregar(nodo.lineno, nodo.value)
                    elif not es_nivel_mod and t.id.startswith(PREFIJOS_VAR):
                        _agregar(nodo.lineno, nodo.value)
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            if es_nivel_mod and nodo.target.id.startswith(PREFIJOS_CONST):
                if consts_permitidas is None or nodo.target.id in consts_permitidas:
                    _agregar(nodo.lineno, nodo.value)
            elif not es_nivel_mod and nodo.target.id.startswith(PREFIJOS_VAR):
                _agregar(nodo.lineno, nodo.value)
        elif isinstance(nodo, ast.Call) and _es_llamada_enviar_wpp(nodo):
            if len(nodo.args) >= 2:
                _agregar(nodo.lineno, nodo.args[1])
            for kw in nodo.keywords:
                if kw.arg in ("mensaje", "texto"):
                    _agregar(nodo.lineno, kw.value)

        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, ast.stmt) and isinstance(
                nodo, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)
            ):
                _visitar_nodo(hijo, es_nivel_mod, es_fn_texto)
            else:
                _visitar_nodo(hijo, es_nivel_mod, es_fn_texto)

    _visitar_stmts(mod.body, True, False)
    return encontrados


def test_reglas_texto_estatico_dt4():
    archivos = _archivos_alcance()
    total_textos = 0
    problemas: list[str] = []

    for ruta in archivos:
        rel = ruta.relative_to(RAIZ).as_posix()
        for lineno, txt in _juntar_textos_archivo(ruta):
            total_textos += 1
            cods = [
                c
                for c in problemas_texto_wpp(txt)
                if not c.startswith(("R1_LINEA_LARGA", "R11_SIN_RELLENAR"))
            ]
            if cods:
                problemas.append(f"{rel}:{lineno} {cods} -> {txt!r}")

    print(f"\nTextos revisados por auditoría estática: {total_textos}")
    assert total_textos >= 150, f"Se esperaban >= 150 textos y hubo {total_textos}"
    assert not problemas, "Problemas detectados:\n" + "\n".join(problemas)
