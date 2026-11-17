"""Control automático de textos de error del servidor (DE.4.1, DE.4.2, DE.4.3)."""
from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from limits.storage import MemoryStorage
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

if not hasattr(PGUUID, "_compiler_dispatcher") or (
    "sqlite" not in PGUUID._compiler_dispatcher.specs
):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"


if not hasattr(JSONB, "_compiler_dispatcher") or (
    "sqlite" not in JSONB._compiler_dispatcher.specs
):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"


from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.core.reglas_texto_web import problemas_texto_web
from app.main import app
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario

RAIZ = Path(__file__).resolve().parents[1]
RUTA_APP = RAIZ / "app"
MANEJADORES_MAIN = {
    "rate_limit_exceeded_handler",
    "validation_exception_handler",
    "http_exception_handler",
}
DINAMICOS_ESPERADOS = 12

# Lista fija de textos que pueden llegar a str(e) / variables dinámicas (Tarea A).
TEXTOS_EXCEPCIONES_TAREA_A: list[tuple[str, str]] = [
    (
        "app/services/email_service.py::verificar_codigo_email",
        "El código ya expiró. Pedí uno nuevo.",
    ),
    (
        "app/services/email_service.py::verificar_codigo_email",
        "Demasiados intentos fallidos. Pedí un código nuevo.",
    ),
    (
        "app/services/email_service.py::verificar_codigo_email",
        "Código incorrecto. Te quedan 2 intentos.",
    ),
    (
        "app/services/email_service.py::verificar_codigo_email",
        "Código incorrecto. Te quedan 1 intento.",
    ),
    ("app/services/onboarding_service.py::validar_ciclo", "El día tiene que ser del 1 al 31."),
    (
        "app/services/onboarding_service.py::validar_ciclo",
        "El día tiene que ser un número del 1 al 31.",
    ),
    ("app/services/onboarding_service.py::validar_ciclo", "Elegí cómo empieza tu mes."),
    (
        "app/services/notificacion_service.py::marcar_leida",
        "No se encontró esa notificación.",
    ),
    (
        "app/services/notificacion_service.py::marcar_no_leida",
        "No se encontró esa notificación.",
    ),
    (
        "app/services/notificacion_service.py::silenciar_notificacion",
        "No se encontró esa notificación.",
    ),
    (
        "app/services/notificacion_service.py::archivar_notificacion",
        "No se encontró esa notificación.",
    ),
    (
        "app/services/notificacion_service.py::eliminar_notificacion",
        "No se encontró esa notificación.",
    ),
    (
        "app/services/transferencia_service.py::calcular_monto_destino",
        "La cotización solo aplica entre pesos y dólares.",
    ),
    (
        "app/services/fecha_saldo_service.py::_validar_y_obtener_billetera",
        "Este saldo ya lo actualizaste el X: los movimientos anteriores no lo cambian.",
    ),
    (
        "app/services/usuario_service.py::actualizar_password",
        "La contraseña no puede estar vacía.",
    ),
    (
        "app/services/usuario_service.py::actualizar_password",
        "La contraseña tiene que tener al menos 8 caracteres.",
    ),
    (
        "app/services/usuario_service.py::actualizar_password",
        "La contraseña no puede superar los 72 bytes.",
    ),
    (
        "app/services/usuario_service.py::actualizar_password",
        "La contraseña no puede superar los 128 caracteres.",
    ),
    (
        "app/services/usuario_service.py::actualizar_password",
        "La contraseña tiene que tener una letra mayúscula.",
    ),
    (
        "app/services/usuario_service.py::actualizar_password",
        "La contraseña tiene que tener una letra minúscula.",
    ),
    (
        "app/services/usuario_service.py::actualizar_password",
        "La contraseña tiene que tener un número.",
    ),
]


def _problemas_sin_w8(texto: str) -> list[str]:
    return [c for c in problemas_texto_web(texto) if not c.startswith("W8_SIN_RELLENAR")]


def _nombre_modulo_a_ruta(mod_name: str) -> Path | None:
    if not mod_name or not mod_name.startswith("app."):
        return None
    cand = RAIZ / (mod_name.replace(".", "/") + ".py")
    return cand if cand.is_file() else None


def _constantes_modulo(mod: ast.Module) -> dict[str, ast.AST]:
    res: dict[str, ast.AST] = {}
    for st in mod.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name):
                    res[t.id] = st.value
        elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name) and st.value:
            res[st.target.id] = st.value
    return res


def _imports_app(mod: ast.Module, base_ruta: Path) -> dict[str, tuple[Path, str]]:
    res: dict[str, tuple[Path, str]] = {}
    for st in mod.body:
        if isinstance(st, ast.ImportFrom) and st.module:
            ruta = _nombre_modulo_a_ruta(st.module)
            if not ruta:
                continue
            for alias in st.names:
                local_name = alias.asname or alias.name
                res[local_name] = (ruta, alias.name)
    return res


def _render_nodo_simple(nodo: ast.AST) -> list[str]:
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
        izqs = _render_nodo_simple(nodo.left)
        ders = _render_nodo_simple(nodo.right)
        if izqs and ders:
            return [i + d for i in izqs for d in ders]
        return izqs or ders
    return []


def _extraer_textos_de_nodo(
    nodo: ast.AST | None,
    consts_locales: dict[str, ast.AST],
    imports_locales: dict[str, tuple[Path, str]],
    cache_mods: dict[Path, tuple[dict[str, ast.AST], dict[str, tuple[Path, str]]]],
    visitados: set[tuple[Path | None, str]],
) -> tuple[list[str], int]:
    """Extrae textos resolvibles y cuenta cuántos no pudieron resolverse (DINÁMICO)."""
    if nodo is None:
        return [], 1

    if isinstance(nodo, (ast.Constant, ast.JoinedStr, ast.BinOp)):
        txts = _render_nodo_simple(nodo)
        if txts:
            return txts, 0
        return [], 1

    if isinstance(nodo, ast.IfExp):
        t1, d1 = _extraer_textos_de_nodo(
            nodo.body, consts_locales, imports_locales, cache_mods, visitados
        )
        t2, d2 = _extraer_textos_de_nodo(
            nodo.orelse, consts_locales, imports_locales, cache_mods, visitados
        )
        return t1 + t2, d1 + d2

    if isinstance(nodo, ast.Dict):
        textos: list[str] = []
        dinamicos = 0
        for k_node, v_node in zip(nodo.keys, nodo.values):
            k_str = k_node.value if isinstance(k_node, ast.Constant) else None
            if k_str in ("message", "detail"):
                t_val, d_val = _extraer_textos_de_nodo(
                    v_node, consts_locales, imports_locales, cache_mods, visitados
                )
                textos.extend(t_val)
                dinamicos += d_val
            elif isinstance(v_node, ast.Dict):
                t_sub, d_sub = _extraer_textos_de_nodo(
                    v_node, consts_locales, imports_locales, cache_mods, visitados
                )
                textos.extend(t_sub)
                dinamicos += d_sub
        return textos, dinamicos

    if isinstance(nodo, ast.Name):
        nombre = nodo.id
        if nombre in consts_locales and (None, nombre) not in visitados:
            sig = set(visitados)
            sig.add((None, nombre))
            return _extraer_textos_de_nodo(
                consts_locales[nombre], consts_locales, imports_locales, cache_mods, sig
            )
        if nombre in imports_locales:
            ruta_ext, orig_name = imports_locales[nombre]
            if (ruta_ext, orig_name) not in visitados:
                if ruta_ext not in cache_mods:
                    mod_ext = ast.parse(
                        ruta_ext.read_text(encoding="utf-8"),
                        filename=str(ruta_ext),
                    )
                    cache_mods[ruta_ext] = (
                        _constantes_modulo(mod_ext),
                        _imports_app(mod_ext, ruta_ext),
                    )
                c_ext, i_ext = cache_mods[ruta_ext]
                if orig_name in c_ext:
                    sig = set(visitados)
                    sig.add((ruta_ext, orig_name))
                    return _extraer_textos_de_nodo(c_ext[orig_name], c_ext, i_ext, cache_mods, sig)
        return [], 1

    return [], 1


def _extraer_arg_call(call: ast.Call, kw_name: str, pos_idx: int) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == kw_name:
            return kw.value
    if len(call.args) > pos_idx:
        return call.args[pos_idx]
    return None


def _extraer_textos_manejadores_main(
    mod: ast.Module,
    consts_locales: dict[str, ast.AST],
    imports_locales: dict[str, tuple[Path, str]],
    cache_mods: dict[Path, tuple[dict[str, ast.AST], dict[str, tuple[Path, str]]]],
) -> tuple[list[tuple[int, str]], int]:
    """Extrae los textos de JSONResponse en los 3 manejadores de errores de app/main.py."""
    textos: list[tuple[int, str]] = []
    dinamicos = 0
    for st in mod.body:
        if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if st.name not in MANEJADORES_MAIN:
            continue
        vars_fn: dict[str, ast.AST] = {}
        for sub in ast.walk(st):
            if isinstance(sub, ast.Assign):
                for t in sub.targets:
                    if isinstance(t, ast.Name):
                        vars_fn[t.id] = sub.value
        for sub in ast.walk(st):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = (
                fn.attr
                if isinstance(fn, ast.Attribute)
                else (fn.id if isinstance(fn, ast.Name) else None)
            )
            if name != "JSONResponse":
                continue
            c_node = _extraer_arg_call(sub, "content", 1)
            if not isinstance(c_node, ast.Dict):
                continue
            # Resolver variables locales como `mensaje = exc.detail if ... else "..."`
            consts_con_fn = {**consts_locales}
            for v_k, v_val in vars_fn.items():
                if isinstance(v_val, ast.IfExp) and isinstance(v_val.orelse, ast.Constant):
                    consts_con_fn[v_k] = v_val.orelse
            txts, dins = _extraer_textos_de_nodo(
                c_node, consts_con_fn, imports_locales, cache_mods, set()
            )
            for t in txts:
                textos.append((sub.lineno, t))
            dinamicos += dins
    return textos, dinamicos


def revisar_fuente_errores(
    fuente: str,
    rel: str = "<memoria>",
    es_main: bool = False,
) -> tuple[int, int, int, list[str]]:
    """Revisa llamadas a HTTPException (y manejadores de app/main.py) en un código fuente."""
    mod = ast.parse(fuente, filename=rel)
    consts = _constantes_modulo(mod)
    imports = _imports_app(mod, RAIZ / rel)
    cache_mods: dict[Path, tuple[dict[str, ast.AST], dict[str, tuple[Path, str]]]] = {}

    llamadas = 0
    textos_revisados = 0
    dinamicos = 0
    problemas: list[str] = []

    for sub in ast.walk(mod):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        name = (
            fn.attr
            if isinstance(fn, ast.Attribute)
            else (fn.id if isinstance(fn, ast.Name) else None)
        )
        if name != "HTTPException":
            continue
        llamadas += 1
        d_node = _extraer_arg_call(sub, "detail", 1)
        txts, dins = _extraer_textos_de_nodo(d_node, consts, imports, cache_mods, set())
        dinamicos += dins
        for t in txts:
            textos_revisados += 1
            cods = _problemas_sin_w8(t)
            if cods:
                problemas.append(f"{rel}:{sub.lineno} {cods} -> {t!r}")

    if es_main:
        txts_main, dins_main = _extraer_textos_manejadores_main(mod, consts, imports, cache_mods)
        dinamicos += dins_main
        for lineno, t in txts_main:
            textos_revisados += 1
            cods = _problemas_sin_w8(t)
            if cods:
                problemas.append(f"{rel}:{lineno} {cods} -> {t!r}")

    return llamadas, textos_revisados, dinamicos, problemas


def test_te1_control_interno_estatico():
    ejemplo = (
        "from fastapi import HTTPException\n"
        "def f(e):\n"
        '    raise HTTPException(status_code=404, detail="Billetera no encontrada")\n'
        '    raise HTTPException(status_code=400, detail={"success": False, '
        '"error": {"code": "X", "message": "Por favor, intentá de nuevo."}})\n'
        "    raise HTTPException(status_code=400, detail=str(e))\n"
    )
    llamadas, _, dinamicos, probs = revisar_fuente_errores(ejemplo, "ejemplo.py")
    assert llamadas == 3
    assert len(probs) == 2
    assert dinamicos == 1


def test_errores_app_cumplen_reglas_web():
    total_llamadas = 0
    total_textos = 0
    total_dinamicos = 0
    problemas: list[str] = []

    for ruta in sorted(RUTA_APP.rglob("*.py")):
        rel = ruta.relative_to(RAIZ).as_posix()
        if rel == "app/routers/whatsapp_ia.py" or rel.startswith("app/routers/whatsapp/"):
            continue
        fuente = ruta.read_text(encoding="utf-8")
        llamadas, txts, dins, probs = revisar_fuente_errores(
            fuente,
            rel,
            es_main=(rel == "app/main.py"),
        )
        total_llamadas += llamadas
        total_textos += txts
        total_dinamicos += dins
        problemas.extend(probs)

    for origen, txt in TEXTOS_EXCEPCIONES_TAREA_A:
        total_textos += 1
        cods = _problemas_sin_w8(txt)
        if cods:
            problemas.append(f"{origen} {cods} -> {txt!r}")

    print(
        f"\nHTTPException revisadas: {total_llamadas}; "
        f"textos: {total_textos}; DINAMICOS: {total_dinamicos}"
    )
    assert total_llamadas >= 380, f"Se esperaban >= 380 HTTPException, hubo {total_llamadas}"
    assert total_dinamicos == DINAMICOS_ESPERADOS, (
        f"Se esperaban {DINAMICOS_ESPERADOS} DINÁMICOS, hubo {total_dinamicos}"
    )
    assert not problemas, "Problemas detectados:\n" + "\n".join(problemas)


# ---------------------------------------------------------------------------
# Fixtures SQLite + TestClient para DE.4.2 y DE.4.3
# ---------------------------------------------------------------------------

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture
def client_con_usuario():
    Base.metadata.create_all(bind=_engine)
    db = _TestingSessionLocal()
    usuario = Usuario(
        id=uuid4(),
        nombre="Ana",
        apellido="Prueba",
        email=f"ana_{uuid4().hex[:8]}@prueba.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        onboarding_completo=True,
        moneda_principal=Moneda.ARS,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    def _override_db():
        s = _TestingSessionLocal()
        try:
            yield s
        finally:
            s.close()

    prev_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: usuario
    try:
        with TestClient(app) as client:
            yield client, usuario, db
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prev_overrides)
        db.close()
        Base.metadata.drop_all(bind=_engine)


def _mensaje_respuesta(resp) -> str:
    data = resp.json()
    if isinstance(data, dict):
        if "error" in data and isinstance(data["error"], dict) and "message" in data["error"]:
            return data["error"]["message"]
        if "detail" in data:
            return str(data["detail"])
    return str(data)


def test_de4_2_manejador_rate_limit(monkeypatch, client_con_usuario):
    client, _, _ = client_con_usuario
    mem = MemoryStorage()
    monkeypatch.setattr(limiter, "_storage", mem)
    monkeypatch.setattr(limiter._limiter, "storage", mem)
    monkeypatch.setattr(limiter, "enabled", True)
    resp = None
    for idx in range(12):
        resp = client.post(
            "/auth/login",
            json={
                "email": f"rl_{idx}@prueba.com",
                "password": "Password123",
            },
        )
        if resp.status_code == 429:
            break
    assert resp is not None and resp.status_code == 429
    assert (
        _mensaje_respuesta(resp)
        == "Hiciste muchos pedidos seguidos. Esperá un momento y probá de nuevo."
    )


def test_de4_2_manejador_validacion(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.post("/auth/login", json={})
    assert resp.status_code == 422
    assert _mensaje_respuesta(resp) == "Hay datos con errores. Revisalos y probá de nuevo."


def test_de4_2_manejador_http_exception(client_con_usuario):
    client, _, _ = client_con_usuario
    ruta_prueba = "/_test_http_exc_no_str"

    def _endpoint_prueba():
        raise HTTPException(status_code=400, detail=["no_es_string"])

    app.add_api_route(ruta_prueba, _endpoint_prueba, methods=["GET"])
    try:
        resp = client.get(ruta_prueba)
    finally:
        app.router.routes = [
            r
            for r in app.router.routes
            if not (hasattr(r, "path") and r.path == ruta_prueba)
        ]
    assert resp.status_code == 400
    assert _mensaje_respuesta(resp) == "No se pudo hacer el pedido. Probá de nuevo."


def test_de4_3_endpoint_billeteras(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.post(
        "/billeteras",
        json={"nombre": "Ef", "moneda": "ARS", "es_efectivo": True, "tna": 10},
    )
    assert resp.status_code == 400
    assert _mensaje_respuesta(resp) == "Una billetera de efectivo no puede tener tasa."


def test_de4_3_endpoint_cuotas_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.post(f"/grupos-cuotas/{uuid4()}/cancelar")
    assert resp.status_code == 404
    assert _mensaje_respuesta(resp) == "No se encontró esa compra en cuotas."


def test_de4_3_endpoint_meta_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.get(f"/goals/{uuid4()}")
    assert resp.status_code == 404
    assert _mensaje_respuesta(resp) == "No se encontró esa meta."


def test_de4_3_endpoint_pago_resumen_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.get(f"/tarjetas/{uuid4()}/simular-pesificacion")
    assert resp.status_code == 404
    assert _mensaje_respuesta(resp) == "No se encontró esa tarjeta."


def test_de4_3_endpoint_presupuesto_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.get(f"/presupuestos/{uuid4()}")
    assert resp.status_code == 404
    assert _mensaje_respuesta(resp) == "No se encontró ese presupuesto."


def test_de4_3_endpoint_suscripcion_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.get(f"/suscripciones/{uuid4()}")
    assert resp.status_code == 404
    assert _mensaje_respuesta(resp) == "No se encontró esa suscripción."


def test_de4_3_endpoint_transaccion_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.get(f"/transacciones/{uuid4()}")
    assert resp.status_code == 404
    assert _mensaje_respuesta(resp) == "No se encontró esa transacción."


def test_de4_3_endpoint_transferencia_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.get(f"/transferencias/{uuid4()}")
    assert resp.status_code == 404
    assert _mensaje_respuesta(resp) == "No se encontró esa transferencia."


def test_de4_3_endpoint_usuario_service(client_con_usuario):
    client, _, _ = client_con_usuario
    resp = client.put(
        "/usuarios/me/datos-personales",
        json={
            "nombre": "Ana",
            "apellido": "Prueba",
            "fecha_nacimiento": "2015-01-01",
        },
    )
    assert resp.status_code == 400
    assert _mensaje_respuesta(resp) == "Tenés que ser mayor de 18 años para crear una cuenta."


def test_de4_3_endpoint_auth(monkeypatch, client_con_usuario):
    client, _, _ = client_con_usuario
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.routers.auth.verificar_turnstile_token", lambda *a, **k: False)
    resp = client.post(
        "/auth/register",
        json={
            "nombre": "Ana",
            "apellido": "Prueba",
            "email": "nuevo_error@prueba.com",
            "password": "Password123",
            "turnstile_token": "bad",
        },
    )
    assert resp.status_code == 400
    assert (
        _mensaje_respuesta(resp)
        == "No se pudo hacer la verificación de seguridad. Probá de nuevo."
    )
