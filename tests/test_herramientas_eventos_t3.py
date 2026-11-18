# -*- coding: utf-8 -*-
"""
tests/test_herramientas_eventos_t3.py — Medición de herramientas por WhatsApp (TE1 a TE7).
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
from app.models.evento_uso import EventoUso
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
from app.routers.whatsapp.handlers_conveniencia import (
    manejar_consulta_conveniencia,
    manejar_respuesta_pendiente_conveniencia,
)
from app.routers.whatsapp.handlers_permitirse import manejar_consulta_permitirse
from app.services.reporte_uso_service import armar_texto, calcular_reporte
from app.services.uso_service import EVENTOS
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
        "app.services.uso_service",
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
        email="wpp_t3@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        telefono="+5491100003333",
        telefono_normalizado="5491100003333",
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
        from_number="+5491100003333",
        msg_type="text",
        t_inicio=0.0,
        db=db,
        usuario=usuario,
        mensaje_texto=texto,
    )


def test_te1_eventos_contiene_eventos_wpp():
    """TE1: EVENTOS contiene permitirse_wpp y cuotas_contado_wpp."""
    assert "permitirse_wpp" in EVENTOS
    assert "cuotas_contado_wpp" in EVENTOS


def test_te2_cuotas_contado_completa_e_idempotente(db: Session, usuario_wpp: Usuario):
    """TE2: consulta completa deja 1 fila en el día; segunda consulta igual sigue en 1."""
    esperada = (
        "Te conviene de contado.\n"
        "12 cuotas de $50.000 son $600.000: $100.000 más que de contado (20%).\n"
        "Con la inflación que se espera, en plata de hoy son unos $529.000."
    )
    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 de 50 mil?", "te2_1")
        )
        mock_envio.assert_called_once()
        assert mock_envio.call_args[0][1] == esperada

        filas = (
            db.query(EventoUso)
            .filter(
                EventoUso.usuario_id == usuario_wpp.id,
                EventoUso.evento == "cuotas_contado_wpp",
                EventoUso.dia == HOY_FIJO,
            )
            .all()
        )
        assert len(filas) == 1

        mock_envio.reset_mock()
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 de 50 mil?", "te2_2")
        )
        mock_envio.assert_called_once()
        assert mock_envio.call_args[0][1] == esperada

        filas_despues = (
            db.query(EventoUso)
            .filter(
                EventoUso.usuario_id == usuario_wpp.id,
                EventoUso.evento == "cuotas_contado_wpp",
                EventoUso.dia == HOY_FIJO,
            )
            .all()
        )
        assert len(filas_despues) == 1


def test_te3_cuotas_contado_pendiente_y_respuesta(db: Session, usuario_wpp: Usuario):
    """TE3: pregunta pendiente deja 0 filas; al contestar 'de 50 mil' queda 1."""
    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 cuotas?", "te3_1")
        )
        mock_envio.assert_called_once()
        filas_pend = (
            db.query(EventoUso)
            .filter(
                EventoUso.usuario_id == usuario_wpp.id,
                EventoUso.evento == "cuotas_contado_wpp",
            )
            .all()
        )
        assert len(filas_pend) == 0

        mock_envio.reset_mock()
        assert manejar_respuesta_pendiente_conveniencia(
            _ctx(db, usuario_wpp, "de 50 mil", "te3_2")
        )
        mock_envio.assert_called_once()
        filas_resp = (
            db.query(EventoUso)
            .filter(
                EventoUso.usuario_id == usuario_wpp.id,
                EventoUso.evento == "cuotas_contado_wpp",
                EventoUso.dia == HOY_FIJO,
            )
            .all()
        )
        assert len(filas_resp) == 1


def test_te4_permitirse_completa_y_pendiente(db: Session, usuario_wpp: Usuario):
    """TE4: pendiente de permitirse deja 0 filas; consulta completa deja 1 fila."""
    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
        assert manejar_consulta_permitirse(
            _ctx(db, usuario_wpp, "¿me lo puedo permitir?", "te4_1")
        )
        mock_envio.assert_called_once()
        filas_pend = (
            db.query(EventoUso)
            .filter(
                EventoUso.usuario_id == usuario_wpp.id,
                EventoUso.evento == "permitirse_wpp",
            )
            .all()
        )
        assert len(filas_pend) == 0

        mock_envio.reset_mock()
        assert manejar_consulta_permitirse(
            _ctx(db, usuario_wpp, "¿me puedo permitir una tele de 300.000?", "te4_2")
        )
        mock_envio.assert_called_once()
        filas_comp = (
            db.query(EventoUso)
            .filter(
                EventoUso.usuario_id == usuario_wpp.id,
                EventoUso.evento == "permitirse_wpp",
                EventoUso.dia == HOY_FIJO,
            )
            .all()
        )
        assert len(filas_comp) == 1


def test_te5_error_cuotas_contado_no_registra_evento(db: Session, usuario_wpp: Usuario):
    """TE5: si inflacion_esperada falla, responde el error de siempre y deja 0 filas."""
    with (
        patch(
            "app.routers.whatsapp.handlers_conveniencia.inflacion_esperada",
            side_effect=RuntimeError("Fallo simulado"),
        ),
        patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio,
    ):
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 de 50 mil?", "te5_1")
        )
        mock_envio.assert_called_once_with(
            "+5491100003333",
            "No pude calcularlo ahora. Probá de nuevo en unos minutos.",
        )
        filas = (
            db.query(EventoUso)
            .filter(EventoUso.usuario_id == usuario_wpp.id)
            .all()
        )
        assert len(filas) == 0


def test_te6_sin_montos_ni_textos_y_un_mensaje(db: Session, usuario_wpp: Usuario):
    """TE6: eventos_uso solo guarda usuario, evento y día; un solo mensaje por consulta."""
    cols = {c.name for c in EventoUso.__table__.columns}
    assert cols == {"id", "usuario_id", "evento", "dia", "fecha"}

    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio_conv:
        assert manejar_consulta_conveniencia(
            _ctx(db, usuario_wpp, "¿me conviene 500 mil contado o 12 de 50 mil?", "te6_1")
        )
        assert mock_envio_conv.call_count == 1

    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio_perm:
        assert manejar_consulta_permitirse(
            _ctx(db, usuario_wpp, "¿me puedo permitir una tele de 300.000?", "te6_2")
        )
        assert mock_envio_perm.call_count == 1

    filas = db.query(EventoUso).filter(EventoUso.usuario_id == usuario_wpp.id).all()
    assert {(f.usuario_id, f.evento, f.dia) for f in filas} == {
        (usuario_wpp.id, "cuotas_contado_wpp", HOY_FIJO),
        (usuario_wpp.id, "permitirse_wpp", HOY_FIJO),
    }


def test_te7_reporte_semanal_linea_whatsapp(db: Session):
    """TE7: el reporte cuenta personas en la semana y agrega la línea de WhatsApp."""
    u1 = Usuario(
        id=uuid4(),
        email="rep_t3_1@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    u2 = Usuario(
        id=uuid4(),
        email="rep_t3_2@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add_all([u1, u2])
    db.flush()

    # Semana para hoy=2026-11-17: del 2026-11-10 al 2026-11-16
    db.add_all([
        EventoUso(usuario_id=u1.id, evento="permitirse_wpp", dia=date(2026, 11, 11)),
        EventoUso(usuario_id=u1.id, evento="permitirse_wpp", dia=date(2026, 11, 12)),
        EventoUso(usuario_id=u2.id, evento="permitirse_wpp", dia=date(2026, 11, 13)),
        EventoUso(usuario_id=u1.id, evento="cuotas_contado_wpp", dia=date(2026, 11, 14)),
    ])
    db.commit()

    rep = calcular_reporte(db, hoy=HOY_FIJO)
    assert rep["funciones"]["permitirse_wpp"] == 2
    assert rep["funciones"]["cuotas_contado_wpp"] == 1

    texto = armar_texto(rep, hoy=HOY_FIJO)
    linea_web = (
        "Funciones de la web (personas en la semana): ¿Me lo puedo permitir? 0; "
        "calculadora de cuotas 0; descargar mis datos 0; Excel de presupuestos 0."
    )
    linea_wpp = (
        "Por WhatsApp (personas en la semana): "
        "¿Me lo puedo permitir? 2; cuotas vs contado 1."
    )
    assert f"{linea_web}\n{linea_wpp}\n\n" in texto
