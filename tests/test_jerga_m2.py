# -*- coding: utf-8 -*-
"""
tests/test_jerga_m2.py — Pruebas de D-M2.1 (TJ1 a TJ4).
"""
from __future__ import annotations

import ast
from decimal import Decimal
import re
from types import SimpleNamespace
import unicodedata
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core import jerga
from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import (
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import (
    AuthProvider,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.routers.whatsapp import (
    deshacer_corregir,
    dolares_desde_pesos,
    handlers_conveniencia,
    handlers_permitirse,
    montos_palabras,
    parsers,
    registro,
    reintegro_wpp,
    transferencias_flujo,
    transferencias_flujo_b,
    transferencias_texto,
)
from app.services import ai_service
from app.utils.fecha import hoy_argentina

CASOS_PALABRAS = [
    ("2 lucas", Decimal("2000")),
    ("1,5 palos", Decimal("1500000")),
    ("80k", Decimal("80000")),
    ("3 mil", Decimal("3000")),
    ("1 melón", Decimal("1000000")),
]


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def bloquear_openai(monkeypatch):
    """Falla si algún test intenta instanciar un cliente real de OpenAI."""
    orig_uuid_proc = types.Uuid.bind_processor

    def _safe_uuid_proc(self, dialect):
        proc = orig_uuid_proc(self, dialect)
        if proc is None:
            return None

        def _proc(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return _proc

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_proc)
    import sqlite3

    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    def _prohibido(*args, **kwargs):
        raise AssertionError("Prohibido crear cliente real de OpenAI en tests.")

    monkeypatch.setattr("app.services.openai_client.OpenAI", _prohibido)
    monkeypatch.setattr("app.services.openai_client._openai_client", None)


@pytest.fixture()
def db_sqlite(monkeypatch):
    """Sesión SQLite en memoria con usuario, billeteras y envío mockeado."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models

    _ = app.models
    Base.metadata.create_all(bind=engine)
    SessionTest = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionTest()

    usuario = Usuario(
        id=uuid.uuid4(),
        email=f"jerga_{uuid.uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="Jerga",
    )
    db.add(usuario)
    db.flush()

    b_ars = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000"),
        saldo_inicial=Decimal("500000"),
        estado=EstadoBilletera.ACTIVA,
        es_principal=True,
    )
    b_usd = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario.id,
        nombre="Efectivo Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("1000"),
        saldo_inicial=Decimal("1000"),
        estado=EstadoBilletera.ACTIVA,
        es_efectivo=True,
    )
    db.add_all([b_ars, b_usd])
    db.commit()

    monkeypatch.setattr(
        "app.services.whatsapp_service.enviar_whatsapp",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        "app.routers.whatsapp.transferencias_flujo_b._obtener_cotizacion_referencia_usuario",
        lambda *args, **kwargs: None,
    )
    try:
        yield SimpleNamespace(db=db, usuario=usuario, b_ars=b_ars, b_usd=b_usd)
    finally:
        db.close()


def test_tj1_multiplicadores_y_sufijo_rx():
    """TJ1: MULTIPLICADORES, multiplicador_de y sufijo_multiplicador_rx."""
    assert jerga.MULTIPLICADORES == {
        "mil": 1000,
        "k": 1000,
        "luca": 1000,
        "lucas": 1000,
        "palo": 1000000,
        "palos": 1000000,
        "melón": 1000000,
        "melon": 1000000,
        "melones": 1000000,
    }
    assert jerga.multiplicador_de("lucas") == 1000
    assert jerga.multiplicador_de("melones") == 1000000
    assert jerga.multiplicador_de("gamba") is None

    rx = re.compile(jerga.sufijo_multiplicador_rx(), re.IGNORECASE)
    for valida in (
        "k",
        "mil",
        "luca",
        "lucas",
        "palo",
        "palos",
        "melón",
        "melon",
        "melones",
    ):
        assert rx.fullmatch(valida), f"No matcheó {valida!r}"
    for invalida in ("lu", "pal"):
        assert not rx.fullmatch(invalida), f"Matcheó inválido {invalida!r}"


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_montos_palabras(frase, esperado):
    """TJ2: montos_palabras.buscar_monto."""
    val, _ = montos_palabras.buscar_monto(f"gaste {frase} en el chino")
    assert val == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_parsers(frase, esperado):
    """TJ2: parsers._parsear_monto_argentino."""
    assert parsers._parsear_monto_argentino(frase) == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_registro_precio_contado(frase, esperado):
    """TJ2: registro._extraer_precio_contado."""
    val = registro._extraer_precio_contado(f"de contado {frase}")
    assert val == esperado
    if "," not in frase:
        val_antes = registro._extraer_precio_contado(
            f"3 cuotas de 10k {frase} de contado"
        )
        assert val_antes == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_deshacer_corregir(db_sqlite, frase, esperado):
    """TJ2: deshacer_corregir._detectar_correccion_ultimo_movimiento."""
    tx = Transaccion(
        id=uuid.uuid4(),
        usuario_id=db_sqlite.usuario.id,
        billetera_id=db_sqlite.b_ars.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500"),
        moneda=Moneda.ARS,
        fecha=hoy_argentina(),
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.IA_WPP,
        descripcion="Kiosco",
    )
    db_sqlite.db.add(tx)
    db_sqlite.db.commit()

    es_corr, cambios, _ = deshacer_corregir._detectar_correccion_ultimo_movimiento(
        f"eran {frase}",
        db_sqlite.usuario.id,
        db_sqlite.db,
        tx,
    )
    assert es_corr is True
    assert Decimal(str(cambios["monto"])) == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_dolares_desde_pesos(frase, esperado):
    """TJ2: dolares_desde_pesos.extraer_monto_pesos."""
    assert dolares_desde_pesos.extraer_monto_pesos(f"fueron {frase} pesos") == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_reintegro_wpp(frase, esperado):
    """TJ2: reintegro_wpp.detectar_pedido_reintegro."""
    res = reintegro_wpp.detectar_pedido_reintegro(
        f"me devolvieron {frase} del super"
    )
    assert res is not None
    assert res[0] == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_handlers_conveniencia(frase, esperado):
    """TJ2: handlers_conveniencia.analizar_consulta_conveniencia."""
    res = handlers_conveniencia.analizar_consulta_conveniencia(
        f"conviene {frase} de contado o 3 cuotas de 100?"
    )
    assert res is not None
    assert res["precio_contado"] == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_handlers_permitirse(frase, esperado):
    """TJ2: handlers_permitirse.analizar_consulta_permitirse."""
    res = handlers_permitirse.analizar_consulta_permitirse(
        f"me puedo permitir algo de {frase}"
    )
    assert res is not None
    assert res["precio"] == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_transferencias_texto(frase, esperado):
    """TJ2: transferencias_texto._extraer_monto_y_moneda."""
    norm = transferencias_texto.normalizar_basico(f"pase {frase} a galicia")
    val, _, _ = transferencias_texto._extraer_monto_y_moneda(norm)
    assert val == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_transferencias_flujo_b(db_sqlite, frase, esperado):
    """TJ2: transferencias_flujo_b.resolver_transferencia_distinta_moneda."""
    item = {
        "origen": db_sqlite.b_ars.nombre,
        "destino": db_sqlite.b_usd.nombre,
        "monto": Decimal("100"),
        "moneda": "USD",
        "fecha": hoy_argentina(),
    }
    ok = transferencias_flujo_b.resolver_transferencia_distinta_moneda(
        db_sqlite.b_ars,
        db_sqlite.b_usd,
        item,
        f"son {frase} pesos",
        db_sqlite.usuario,
        db_sqlite.db,
        "5491100000000",
        "wamid_test",
    )
    assert ok is True
    conv = db_sqlite.db.query(
        transferencias_flujo_b.ConversacionWpp
    ).order_by(transferencias_flujo_b.ConversacionWpp.fecha.desc()).first()
    assert conv is not None
    assert Decimal(str(conv.entidades["monto_origen"])) == esperado


@pytest.mark.parametrize("frase,esperado", CASOS_PALABRAS)
def test_tj2_transferencias_flujo_monto(db_sqlite, monkeypatch, frase, esperado):
    """TJ2: transferencias_flujo.continuar_transferencia en paso monto."""
    capturado = {}

    def _fake_tabla(item, *args, **kwargs):
        capturado["monto"] = item["monto"]
        return True

    monkeypatch.setattr(transferencias_flujo, "_ejecutar_tabla_flujo", _fake_tabla)
    estado = {
        "paso": "monto",
        "billetera_origen_id": str(db_sqlite.b_ars.id),
        "billetera_destino_id": str(db_sqlite.b_usd.id),
    }
    ok = transferencias_flujo.continuar_transferencia(
        f"{frase} pesos",
        db_sqlite.usuario,
        db_sqlite.db,
        "5491100000000",
        "wamid_test",
        None,
        estado,
    )
    assert ok is True
    assert capturado["monto"] == esperado


def _sin_tildes(txt: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", txt)
        if unicodedata.category(c) != "Mn"
    )


def test_tj3_system_prompt_jerga_sincronizada():
    """TJ3: cada palabra de jerga está en el bloque del prompt y viceversa."""
    sp = ai_service.SYSTEM_PROMPT
    inicio = sp.index("JERGA ARGENTINA QUE DEBÉS ENTENDER:")
    fin = sp.index("- mp / mercadopago / merca = Mercado Pago")
    bloque = sp[inicio:fin].lower()
    bloque_sin_tildes = _sin_tildes(bloque)

    for palabra in (*jerga.MULTIPLICADORES.keys(), *jerga.NO_ES_MONTO):
        if palabra == "k":
            assert re.search(r"\b\d+k\b", bloque)
        elif palabra == "mil":
            assert "miles" in bloque or re.search(r"\bmil\b", bloque)
        elif palabra == "melon":
            assert "melon" in bloque_sin_tildes
        else:
            assert re.search(rf"\b{re.escape(palabra)}\b", bloque), (
                f"Falta {palabra!r} en el bloque de jerga del SYSTEM_PROMPT"
            )

    tokens_prompt = set()
    for linea in bloque.splitlines():
        if re.search(r"\b\d+k\b", linea):
            tokens_prompt.add("k")
        for m in re.finditer(
            r"\b(luca|lucas|palo|palos|mel[oó]n|melones|gamba|gambas)\b",
            linea,
        ):
            tokens_prompt.add(m.group(1))

    jerga_conocida = set(jerga.MULTIPLICADORES.keys()) | set(jerga.NO_ES_MONTO)
    for tok in tokens_prompt:
        assert tok in jerga_conocida, (
            f"Palabra {tok!r} del bloque de jerga falta en jerga.py"
        )


def test_tj4_sin_jerga_hardcodeada_en_app():
    """TJ4: fuera de app/core/jerga.py no hay regex/listas con jerga."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    jerga_file = (app_dir / "core" / "jerga.py").resolve()
    rx_prohibido = re.compile(r"lucas\?|palos\?|mel\[oó\]n")
    rx_mangos = re.compile(r"\bmangos\b", re.IGNORECASE)

    violaciones: list[str] = []
    for py_path in sorted(app_dir.rglob("*.py")):
        if py_path.resolve() == jerga_file:
            continue
        source = py_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_path))

        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    docstrings.add(id(node.body[0].value))

        # Excluir SYSTEM_PROMPT de ai_service.py (verificado por TJ3)
        system_prompt_ids: set[int] = set()
        if py_path.name == "ai_service.py":
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Name) and t.id == "SYSTEM_PROMPT":
                            system_prompt_ids.add(id(node.value))

        padres: dict[int, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                padres[id(child)] = parent

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstrings or id(node) in system_prompt_ids:
                continue
            val = node.value
            rel = py_path.relative_to(app_dir.parent).as_posix()
            if rx_prohibido.search(val):
                violaciones.append(f"{rel}:{node.lineno}: {val!r}")
            elif rx_mangos.search(val):
                padre = padres.get(id(node))
                es_coleccion = isinstance(
                    padre, (ast.List, ast.Tuple, ast.Set, ast.JoinedStr)
                )
                es_regex = any(
                    tok in val for tok in ("|", "\\b", "(?:", "(", "[", "^", "$")
                )
                if es_coleccion or es_regex:
                    violaciones.append(f"{rel}:{node.lineno}: {val!r}")

    assert not violaciones, "Jerga hardcodeada fuera de jerga.py:\n" + "\n".join(
        violaciones
    )
