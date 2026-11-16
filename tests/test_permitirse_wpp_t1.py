# -*- coding: utf-8 -*-
"""
tests/test_permitirse_wpp_t1.py — Detector, flujo pendiente y envíos únicos de T1 (W1 a W5).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import sqlite3
from unittest.mock import patch
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.etapa_handlers import procesar_estados_y_handlers_deterministicos
from app.routers.whatsapp.handlers_permitirse import (
    detectar_consulta_permitirse,
    manejar_consulta_permitirse,
    manejar_respuesta_pendiente_permitirse,
)
from app.routers.whatsapp.permitirse_flujo import buscar_pregunta_pendiente
from app.utils.fecha import TZ_ARGENTINA

HOY_FIJO = date(2026, 11, 17)


@pytest.fixture(autouse=True)
def _compat_y_fecha(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid_processor = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    for mod in (
        "app.utils.fecha",
        "app.services.permitirse_service",
        "app.services.fin_de_mes_service",
        "app.services.frescura_service",
        "app.services.compromisos_service",
        "app.services.ingreso_habitual_service",
        "app.services.definiciones_service",
        "app.services.pagos_proximos_service",
        "app.services.dolar_service",
        "app.services.tools_service",
    ):
        monkeypatch.setattr(f"{mod}.hoy_argentina", lambda: HOY_FIJO, raising=False)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    session.add(CotizacionDolar(
        fecha=HOY_FIJO,
        tipo="oficial",
        compra=Decimal("1400.00"),
        venta=Decimal("1450.00"),
        promedio=Decimal("1425.00"),
        fecha_registro=datetime(2026, 11, 17, 10, 0, tzinfo=TZ_ARGENTINA),
    ))
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="usuario_wpp")
def usuario_wpp_fixture(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email="wpp_t1@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        telefono="+5491100001111",
        telefono_normalizado="5491100001111",
        telefono_verificado=True,
        fecha_registro=datetime(2026, 3, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(u)
    db.flush()
    db.add(Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_actual=Decimal("500000"),
        saldo_inicial=Decimal("500000"),
        fecha_saldo=date(2026, 3, 1),
        tiene_punto_partida=True,
    ))
    db.commit()
    return u


def _ctx(db: Session, usuario: Usuario, texto: str, wamid: str) -> ContextoMensaje:
    return ContextoMensaje(
        datos_mensaje={},
        msg={},
        wamid=wamid,
        from_number="+5491100001111",
        msg_type="text",
        t_inicio=0.0,
        db=db,
        usuario=usuario,
        mensaje_texto=texto,
    )


def test_w1_detector_frases_nuevas():
    casos = [
        ("me da para un celular de 300 mil?", Decimal("300000"), "contado", 1, "ARS"),
        (
            "me puedo dar el gusto de unas zapatillas de 150 lucas?",
            Decimal("150000"), "contado", 1, "ARS",
        ),
        ("llego a comprarme una tele de 500 mil?", Decimal("500000"), "contado", 1, "ARS"),
        ("tengo para comprarme una bici de 400 mil?", Decimal("400000"), "contado", 1, "ARS"),
        ("me sale 250 mil, me lo compro?", Decimal("250000"), "contado", 1, "ARS"),
        ("me alcanza la plata para un viaje de 2 palos?", Decimal("2000000"), "contado", 1, "ARS"),
        (
            "me puedo permitir pagar el gimnasio de 40 mil por mes?",
            Decimal("40000"), "mensual", 1, "ARS",
        ),
        ("me puedo permitir un viaje de 1000 dólares?", Decimal("1000"), "contado", 1, "USD"),
        ("me conviene comprar una heladera de 900 mil?", Decimal("900000"), "contado", 1, "ARS"),
        ("puedo gastar 15 mil al mes en netflix?", Decimal("15000"), "mensual", 1, "ARS"),
        ("me puedo permitir 12 cuotas de 50 lucas?", Decimal("600000"), "cuotas", 12, "ARS"),
    ]
    for texto, precio, modo, cuotas, moneda in casos:
        res = detectar_consulta_permitirse(texto)
        assert res is not None, f"No detectó: {texto}"
        assert res["falta"] is None
        assert res["precio"] == precio
        assert res["modo"] == modo
        assert res["cantidad_cuotas"] == cuotas
        assert res["moneda"] == moneda


@pytest.mark.parametrize(
    "texto",
    [
        "¿puedo comprar 100 dólares?",
        "¿puedo pagar la tarjeta?",
        "¿me alcanza para llegar a fin de mes?",
        "me alcanza la plata?",
        "gasté 5000 en el kiosco",
        "¿puedo comprar algo?",
        "me alcanza para comprar 100 dólares?",
        "compré 100 dólares a 1500",
        "¿llego a fin de mes?",
    ],
)
def test_w2_siguen_dando_none(texto: str):
    assert detectar_consulta_permitirse(texto) is None


def test_w3_pregunta_precio_pendiente_y_cierre(db: Session, usuario_wpp: Usuario):
    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
        assert manejar_consulta_permitirse(_ctx(db, usuario_wpp, "¿me lo puedo permitir?", "w1"))
        mock_envio.assert_called_once()
        assert mock_envio.call_args[0][1] == (
            'Decime cuánto sale. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'
        )
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is not None

        mock_envio.reset_mock()
        assert manejar_respuesta_pendiente_permitirse(_ctx(db, usuario_wpp, "300 mil", "w2"))
        mock_envio.assert_called_once()
        resp = mock_envio.call_args[0][1]
        assert resp.startswith(("Te entra", "Solo tocando", "No te alcanza"))
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is None

        # Nueva pregunta y luego "hola": se cierra y el handler devuelve False
        mock_envio.reset_mock()
        assert manejar_consulta_permitirse(_ctx(db, usuario_wpp, "¿me lo puedo permitir?", "w3"))
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is not None
        assert manejar_respuesta_pendiente_permitirse(_ctx(db, usuario_wpp, "hola", "w4")) is False
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is None


def test_w4_pregunta_cuotas_pendiente(db: Session, usuario_wpp: Usuario):
    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
        assert manejar_consulta_permitirse(
            _ctx(db, usuario_wpp, "me puedo permitir algo en cuotas de 300 mil?", "w5")
        )
        mock_envio.assert_called_once()
        assert mock_envio.call_args[0][1].startswith("Decime en cuántas cuotas.")

        mock_envio.reset_mock()
        ctx_6 = _ctx(db, usuario_wpp, "6 cuotas", "w6")
        procesar_estados_y_handlers_deterministicos(ctx_6)
        assert ctx_6.terminado is True
        mock_envio.assert_called_once()
        assert "6 cuotas" in mock_envio.call_args[0][1]


def test_w5_un_envio_por_mensaje(db: Session, usuario_wpp: Usuario):
    mensajes = [
        "me da para un celular de 300 mil?",
        "me puedo permitir un viaje de 1000 dólares?",
        "me puedo permitir pagar el gimnasio de 40 mil por mes?",
    ]
    for idx, msg in enumerate(mensajes):
        with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
            ctx = _ctx(db, usuario_wpp, msg, f"w5_{idx}")
            procesar_estados_y_handlers_deterministicos(ctx)
            assert ctx.terminado is True
            mock_envio.assert_called_once()
