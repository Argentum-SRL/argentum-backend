# -*- coding: utf-8 -*-
"""tests/test_conveniencia_wpp_t2.py — Pruebas WhatsApp de Cuotas vs contado (TW1 a TW4)."""
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
from app.models.expectativa_inflacion import ExpectativaInflacion
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.conveniencia_flujo import buscar_pregunta_pendiente
from app.routers.whatsapp.etapa_handlers import procesar_estados_y_handlers_deterministicos
from app.routers.whatsapp.handlers_conveniencia import (
    analizar_consulta_conveniencia,
    manejar_consulta_conveniencia,
    manejar_respuesta_pendiente_conveniencia,
)
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

    orig_uuid = types.Uuid.bind_processor

    def _safe_uuid(self, dialect):
        proc = orig_uuid(self, dialect)
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

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid)
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
        "app.services.inflacion_esperada_service",
    ):
        monkeypatch.setattr(f"{mod}.hoy_argentina", lambda: HOY_FIJO, raising=False)

    yield

    if not had_sqlite:
        if (
            hasattr(JSONB, "_compiler_dispatcher")
            and hasattr(JSONB._compiler_dispatcher, "specs")
        ):
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
    for m_idx in range(1, 13):
        anio = HOY_FIJO.year + (HOY_FIJO.month + m_idx - 1) // 12
        mes = (HOY_FIJO.month + m_idx - 1) % 12 + 1
        session.add(ExpectativaInflacion(
            relevamiento="2026-10",
            periodo=f"{anio:04d}-{mes:02d}",
            valor=Decimal("2.0"),
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
        email="wpp_t2@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        telefono="+5491100002222",
        telefono_normalizado="5491100002222",
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
        from_number="+5491100002222",
        msg_type="text",
        t_inicio=0.0,
        db=db,
        usuario=usuario,
        mensaje_texto=texto,
    )


def test_tw1_detector_frases_conveniencia():
    casos = [
        (
            "¿me conviene 500 mil contado o 12 de 50 mil?",
            Decimal("500000"),
            12,
            Decimal("50000"),
            False,
        ),
        (
            "conviene pagar 500 mil de contado o 12 cuotas de 50 mil?",
            Decimal("500000"),
            12,
            Decimal("50000"),
            False,
        ),
        (
            "12 cuotas sin interés de 50 mil o 550 mil de contado, que me conviene?",
            Decimal("550000"),
            12,
            Decimal("50000"),
            True,
        ),
        (
            "me conviene en 6 cuotas sin interés o contado 300 mil?",
            Decimal("300000"),
            6,
            Decimal("50000"),
            True,
        ),
    ]
    for texto, contado, n, cuota, sin_int in casos:
        res = analizar_consulta_conveniencia(texto)
        assert res is not None, f"No detectó: {texto}"
        assert res["falta"] is None
        assert res["precio_contado"] == contado
        assert res["cantidad_cuotas"] == n
        assert res["valor_cuota"] == cuota
        assert res["sin_interes"] is sin_int


def test_tw2_cuota_pendiente_y_respuesta(db: Session, usuario_wpp: Usuario):
    esperada_pregunta = (
        "Decime cuánto es cada cuota o si son sin interés. Por ejemplo: _12 cuotas de 50 mil_"
    )
    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 cuotas?", "tw2_1")
        )
        mock_envio.assert_called_once()
        assert mock_envio.call_args[0][1] == esperada_pregunta
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is not None

        mock_envio.reset_mock()
        assert manejar_respuesta_pendiente_conveniencia(
            _ctx(db, usuario_wpp, "de 50 mil", "tw2_2")
        )
        mock_envio.assert_called_once()
        resp = mock_envio.call_args[0][1]
        assert resp == (
            "Te conviene de contado.\n"
            "12 cuotas de $50.000 son $600.000: $100.000 más que de contado (20%).\n"
            "Con la inflación que se espera, en plata de hoy son unos $529.000."
        )
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is None

        # Otro caso respondiendo solo "50 mil"
        mock_envio.reset_mock()
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 cuotas?", "tw2_3")
        )
        mock_envio.reset_mock()
        ctx_50 = _ctx(db, usuario_wpp, "50 mil", "tw2_4")
        procesar_estados_y_handlers_deterministicos(ctx_50)
        assert ctx_50.terminado is True
        mock_envio.assert_called_once()
        assert "12 cuotas de $50.000 son $600.000" in mock_envio.call_args[0][1]

        # Respuesta no relacionada cierra la pregunta pendiente y devuelve False
        mock_envio.reset_mock()
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 cuotas?", "tw2_5")
        )
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is not None
        assert (
            manejar_respuesta_pendiente_conveniencia(_ctx(db, usuario_wpp, "hola", "tw2_6"))
            is False
        )
        assert buscar_pregunta_pendiente(db, usuario_wpp.id) is None


def test_tw3_no_roba_permitirse(db: Session, usuario_wpp: Usuario):
    assert analizar_consulta_conveniencia("me puedo permitir algo de 300 mil?") is None
    assert (
        analizar_consulta_conveniencia("me conviene comprar una heladera de 900 mil?")
        is None
    )

    for idx, msg in enumerate([
        "me puedo permitir algo de 300 mil?",
        "me conviene comprar una heladera de 900 mil?",
    ]):
        with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
            ctx = _ctx(db, usuario_wpp, msg, f"tw3_{idx}")
            procesar_estados_y_handlers_deterministicos(ctx)
            assert ctx.terminado is True
            mock_envio.assert_called_once()
            resp = mock_envio.call_args[0][1]
            assert not resp.startswith(("Te conviene ", "Da casi lo mismo."))


def test_tw4_un_mensaje_saliente_por_entrante(db: Session, usuario_wpp: Usuario):
    mensajes = [
        "¿me conviene 500 mil contado o 12 de 50 mil?",
        "12 cuotas sin interés de 50 mil o 550 mil de contado, que me conviene?",
        "me conviene en 6 cuotas sin interés o contado 300 mil?",
    ]
    for idx, msg in enumerate(mensajes):
        with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
            ctx = _ctx(db, usuario_wpp, msg, f"tw4_{idx}")
            procesar_estados_y_handlers_deterministicos(ctx)
            assert ctx.terminado is True
            mock_envio.assert_called_once()
