# -*- coding: utf-8 -*-
"""
tests/test_reporte_uso.py — Pruebas del reporte semanal de uso de Argentum.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.config import settings
from app.core.database import Base
from app.core.jobs_catalogo import JOBS_MANUALES_PERMITIDOS, JOBS_POR_ID
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.evento_uso import EventoUso
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.subcategoria import Subcategoria
from app.models.transaccion import OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.uso_ia import UsoIA
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.reporte_uso_service import (
    _job_reporte_uso_semanal,
    armar_texto,
    calcular_reporte,
)
from app.utils.fecha import TZ_ARGENTINA


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
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

    import sqlite3
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
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
    try:
        yield session
    finally:
        session.close()


def _crear_usuario(db: Session, email: str, fecha_reg: datetime, permiso_mensajes: bool = False) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=fecha_reg,
        permiso_lectura_mensajes_en=fecha_reg if permiso_mensajes else None,
    )
    db.add(u)
    db.flush()
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Principal",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("0"),
        saldo_inicial=Decimal("0"),
        es_principal=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b)
    db.commit()
    db.refresh(u)
    setattr(u, "_billetera_test", b)
    return u


def test_reporte_con_datos_completos(db: Session):
    """
    Verifica el cálculo y texto del reporte con el escenario exacto de la tarea D.
    hoy = date(2026, 11, 2)
    """
    hoy = date(2026, 11, 2)

    # Categorías necesarias
    cat_otros = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    cat_alim = Categoria(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO)
    db.add_all([cat_otros, cat_alim])
    db.commit()

    subcat_super = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Supermercado")
    db.add(subcat_super)
    db.commit()

    # U1: alta 27/10 10:00 con una transacción MANUAL creada el 27/10 20:00 y eventos "permitirse" el 28/10 y el 29/10.
    u1_reg = datetime(2026, 10, 27, 10, 0, tzinfo=TZ_ARGENTINA)
    u1 = _crear_usuario(db, "u1@test.com", u1_reg)
    tx_u1 = Transaccion(
        id=uuid4(),
        usuario_id=u1.id,
        billetera_id=u1._billetera_test.id,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=date(2026, 10, 27),
        fecha_creacion=datetime(2026, 10, 27, 20, 0, tzinfo=TZ_ARGENTINA),
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(tx_u1)
    ev_u1_a = EventoUso(usuario_id=u1.id, evento="permitirse", dia=date(2026, 10, 28))
    ev_u1_b = EventoUso(usuario_id=u1.id, evento="permitirse", dia=date(2026, 10, 29))
    db.add_all([ev_u1_a, ev_u1_b])

    # U2: alta 29/10 09:00, una transacción MANUAL creada el 31/10 12:00, una IA_WPP sin subcategoría con descripción "algo" creada el 31/10 13:00 y un evento "descargar_datos" el 31/10.
    u2_reg = datetime(2026, 10, 29, 9, 0, tzinfo=TZ_ARGENTINA)
    u2 = _crear_usuario(db, "u2@test.com", u2_reg)
    tx_u2_manual = Transaccion(
        id=uuid4(),
        usuario_id=u2.id,
        billetera_id=u2._billetera_test.id,
        monto=Decimal("2000.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=date(2026, 10, 31),
        fecha_creacion=datetime(2026, 10, 31, 12, 0, tzinfo=TZ_ARGENTINA),
        origen=OrigenTransaccion.MANUAL,
    )
    tx_u2_wpp = Transaccion(
        id=uuid4(),
        usuario_id=u2.id,
        billetera_id=u2._billetera_test.id,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=date(2026, 10, 31),
        fecha_creacion=datetime(2026, 10, 31, 13, 0, tzinfo=TZ_ARGENTINA),
        origen=OrigenTransaccion.IA_WPP,
        descripcion="algo",
        categoria_id=cat_alim.id,
        subcategoria_id=None,
    )
    ev_u2 = EventoUso(usuario_id=u2.id, evento="descargar_datos", dia=date(2026, 10, 31))
    db.add_all([tx_u2_manual, tx_u2_wpp, ev_u2])

    # U3: alta 14/10 10:00, con permiso_lectura_mensajes_en no nulo, ConversacionWpp el 22/10 11:00 y el 28/10 11:00,
    # una IA_WPP en la categoría "Otros" (egreso) con descripción "kiosko raro" creada el 28/10 12:00 y una IA_WPP en Alimentación > Supermercado creada el 28/10 12:30.
    u3_reg = datetime(2026, 10, 14, 10, 0, tzinfo=TZ_ARGENTINA)
    u3 = _crear_usuario(db, "u3@test.com", u3_reg, permiso_mensajes=True)
    c_u3_a = ConversacionWpp(
        id=uuid4(),
        usuario_id=u3.id,
        fecha=datetime(2026, 10, 22, 11, 0, tzinfo=TZ_ARGENTINA),
        mensaje_usuario="hola",
        mensaje_bot="Hola!",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
    )
    c_u3_b = ConversacionWpp(
        id=uuid4(),
        usuario_id=u3.id,
        fecha=datetime(2026, 10, 28, 11, 0, tzinfo=TZ_ARGENTINA),
        mensaje_usuario="gasto",
        mensaje_bot="Anotado.",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
    )
    tx_u3_otros = Transaccion(
        id=uuid4(),
        usuario_id=u3.id,
        billetera_id=u3._billetera_test.id,
        monto=Decimal("1500.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=date(2026, 10, 28),
        fecha_creacion=datetime(2026, 10, 28, 12, 0, tzinfo=TZ_ARGENTINA),
        origen=OrigenTransaccion.IA_WPP,
        descripcion="kiosko raro",
        categoria_id=cat_otros.id,
    )
    tx_u3_super = Transaccion(
        id=uuid4(),
        usuario_id=u3.id,
        billetera_id=u3._billetera_test.id,
        monto=Decimal("8000.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=date(2026, 10, 28),
        fecha_creacion=datetime(2026, 10, 28, 12, 30, tzinfo=TZ_ARGENTINA),
        origen=OrigenTransaccion.IA_WPP,
        descripcion="super",
        categoria_id=cat_alim.id,
        subcategoria_id=subcat_super.id,
    )
    db.add_all([c_u3_a, c_u3_b, tx_u3_otros, tx_u3_super])

    # U4: alta 15/10 10:00 con eventos "visita" y "permitirse" el 30/10.
    u4_reg = datetime(2026, 10, 15, 10, 0, tzinfo=TZ_ARGENTINA)
    u4 = _crear_usuario(db, "u4@test.com", u4_reg)
    ev_u4_a = EventoUso(usuario_id=u4.id, evento="visita", dia=date(2026, 10, 30))
    ev_u4_b = EventoUso(usuario_id=u4.id, evento="permitirse", dia=date(2026, 10, 30))
    db.add_all([ev_u4_a, ev_u4_b])

    # uso_ia con costo_usd 0.0150 el 27/10, 0.0050 el 30/10 y 1.0000 el 25/10.
    ia_1 = UsoIA(
        id=uuid4(),
        fecha=datetime(2026, 10, 27, 14, 0, tzinfo=TZ_ARGENTINA),
        funcion="analisis",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("0.0150"),
    )
    ia_2 = UsoIA(
        id=uuid4(),
        fecha=datetime(2026, 10, 30, 15, 0, tzinfo=TZ_ARGENTINA),
        funcion="analisis",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("0.0050"),
    )
    ia_fuera = UsoIA(
        id=uuid4(),
        fecha=datetime(2026, 10, 25, 16, 0, tzinfo=TZ_ARGENTINA),
        funcion="analisis",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("1.0000"),
    )
    db.add_all([ia_1, ia_2, ia_fuera])

    # mensajes_salientes_wpp enviados el 29/10: dos con tipo_cobro "regular" y uno con "free_customer_service"; uno "regular" no enviado (enviado False).
    m1 = MensajeSalienteWpp(
        id=uuid4(),
        fecha=datetime(2026, 10, 29, 10, 0, tzinfo=TZ_ARGENTINA),
        tipo="respuesta",
        enviado=True,
        tipo_cobro="regular",
    )
    m2 = MensajeSalienteWpp(
        id=uuid4(),
        fecha=datetime(2026, 10, 29, 11, 0, tzinfo=TZ_ARGENTINA),
        tipo="respuesta",
        enviado=True,
        tipo_cobro="regular",
    )
    m3 = MensajeSalienteWpp(
        id=uuid4(),
        fecha=datetime(2026, 10, 29, 12, 0, tzinfo=TZ_ARGENTINA),
        tipo="respuesta",
        enviado=True,
        tipo_cobro="free_customer_service",
    )
    m4 = MensajeSalienteWpp(
        id=uuid4(),
        fecha=datetime(2026, 10, 29, 13, 0, tzinfo=TZ_ARGENTINA),
        tipo="respuesta",
        enviado=False,
        tipo_cobro="regular",
    )
    db.add_all([m1, m2, m3, m4])
    db.commit()

    rep = calcular_reporte(db, hoy)
    texto = armar_texto(rep, hoy)

    esperado = (
        "Uso de Argentum del 26/10 al 01/11 (sin datos personales).\n\n"
        "Cuentas: 4 en total, 2 nuevas en la semana.\n"
        "Activos en la semana: 4 (web 3, WhatsApp 2).\n"
        "Activación: 1 de 2 cuentas nuevas cargaron algo en sus primeras 24 horas (50%).\n"
        "Retención a 7 días: 1 de 2 (50%).\n"
        "Retención a 30 días: no hay cuentas para medir.\n\n"
        "Costo de la semana: IA USD 0,02; WhatsApp 2 mensajes cobrados por Meta (≈ USD 0,05).\n"
        "Costo por usuario activo: USD 0,0165.\n\n"
        "Funciones de la web (personas en la semana): ¿Me lo puedo permitir? 2; calculadora de cuotas 0; descargar mis datos 1.\n\n"
        "WhatsApp en \"Otros\": 2 de 3 movimientos quedaron en Otros o sin subcategoría.\n"
        "Descripciones (solo de quien dio permiso):\n"
        "- kiosko raro"
    )

    assert texto == esperado


def test_reporte_base_vacia(db: Session):
    """
    Base vacía, mismo hoy: texto exacto.
    """
    hoy = date(2026, 11, 2)
    rep = calcular_reporte(db, hoy)
    texto = armar_texto(rep, hoy)

    esperado = (
        "Uso de Argentum del 26/10 al 01/11 (sin datos personales).\n\n"
        "Cuentas: 0 en total, 0 nuevas en la semana.\n"
        "Activos en la semana: 0 (web 0, WhatsApp 0).\n"
        "Activación: no hubo cuentas nuevas para medir.\n"
        "Retención a 7 días: no hay cuentas para medir.\n"
        "Retención a 30 días: no hay cuentas para medir.\n\n"
        "Costo de la semana: IA USD 0,00; WhatsApp 0 mensajes cobrados por Meta (≈ USD 0,00).\n"
        "Costo por usuario activo: sin usuarios activos.\n\n"
        "Funciones de la web (personas en la semana): ¿Me lo puedo permitir? 0; calculadora de cuotas 0; descargar mis datos 0.\n\n"
        "WhatsApp en \"Otros\": no hubo movimientos por WhatsApp."
    )

    assert texto == esperado


def test_bordes_retencion_7_dias(db: Session):
    """
    Bordes de la retención a 7 días (hoy = 2026-11-02):
    - alta 13/10 con un día activo el 20/10 (alta + 7) → retenido;
    - alta 19/10 con un día activo el 01/11 (alta + 13) → retenido;
    - alta 12/10 o alta 20/10 → fuera de la cohorte.
    """
    hoy = date(2026, 11, 2)

    # 1. alta 13/10 con un día activo el 20/10 (alta + 7) -> retenido
    u_borde_inf = _crear_usuario(db, "borde_inf@test.com", datetime(2026, 10, 13, 10, 0, tzinfo=TZ_ARGENTINA))
    db.add(EventoUso(usuario_id=u_borde_inf.id, evento="visita", dia=date(2026, 10, 20)))

    # 2. alta 19/10 con un día activo el 01/11 (alta + 13) -> retenido
    u_borde_sup = _crear_usuario(db, "borde_sup@test.com", datetime(2026, 10, 19, 10, 0, tzinfo=TZ_ARGENTINA))
    db.add(EventoUso(usuario_id=u_borde_sup.id, evento="visita", dia=date(2026, 11, 1)))

    # 3. alta 12/10 -> fuera de cohorte
    u_fuera_inf = _crear_usuario(db, "fuera_inf@test.com", datetime(2026, 10, 12, 10, 0, tzinfo=TZ_ARGENTINA))
    db.add(EventoUso(usuario_id=u_fuera_inf.id, evento="visita", dia=date(2026, 10, 19)))

    # 4. alta 20/10 -> fuera de cohorte
    u_fuera_sup = _crear_usuario(db, "fuera_sup@test.com", datetime(2026, 10, 20, 10, 0, tzinfo=TZ_ARGENTINA))
    db.add(EventoUso(usuario_id=u_fuera_sup.id, evento="visita", dia=date(2026, 10, 27)))
    db.commit()

    rep = calcular_reporte(db, hoy)
    assert rep["retencion_7"]["n"] == 2
    assert rep["retencion_7"]["r"] == 2
    assert rep["retencion_7"]["p"] == "100%"


def test_job_reporte_emails_y_purga(db: Session, monkeypatch):
    """
    Job: con REPORTE_USO_EMAILS "a@x.com, b@y.com" → _enviar_email (parcheado) llamado 2 veces;
    con REPORTE_USO_EMAILS "" y ADMIN_ALERT_EMAIL "c@z.com" → 1 vez a "c@z.com".
    Los dos vacíos → 0 envíos y False.
    Purga: eventos con dia 27/09/2025 (hoy − 401) se borran y con dia 29/09/2025 (hoy − 399) quedan.
    "reporte_uso_semanal" está en JOBS_POR_ID con puede_correr_ahora True y en JOBS_MANUALES_PERMITIDOS.
    """
    hoy = date(2026, 11, 2)

    # 1. Configuración de job en catálogo
    assert "reporte_uso_semanal" in JOBS_MANUALES_PERMITIDOS
    assert "reporte_uso_semanal" in JOBS_POR_ID
    item_job = JOBS_POR_ID["reporte_uso_semanal"]
    assert item_job.puede_correr_ahora is True
    assert item_job.periodo_esperado_minutos == 10080

    # 2. Purga de eventos antiguos
    uid = uuid4()
    ev_antiguo = EventoUso(usuario_id=uid, evento="visita", dia=date(2025, 9, 27))  # hoy - 401
    ev_vigente = EventoUso(usuario_id=uid, evento="visita", dia=date(2025, 9, 29))  # hoy - 399
    db.add_all([ev_antiguo, ev_vigente])
    db.commit()

    # Mocks de envío y lock
    mock_envio = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.reporte_uso_service._enviar_email", mock_envio)
    monkeypatch.setattr("app.services.reporte_uso_service.intentar_tomar_lock_job", lambda *a, **k: True)
    monkeypatch.setattr("app.services.reporte_uso_service.liberar_lock_job", lambda *a, **k: None)

    # A) REPORTE_USO_EMAILS con 2 correos
    monkeypatch.setattr(settings, "REPORTE_USO_EMAILS", "a@x.com, b@y.com")
    monkeypatch.setenv("ADMIN_ALERT_EMAIL", "")

    res1 = _job_reporte_uso_semanal(db=db, hoy=hoy)
    assert res1 is True
    assert mock_envio.call_count == 2
    args_list = [call[0] for call in mock_envio.call_args_list]
    assert args_list[0][0] == "a@x.com"
    assert args_list[0][1] == "[Argentum] Uso de la semana del 26/10 al 01/11"
    assert args_list[1][0] == "b@y.com"

    # Verificar purga
    restantes = db.query(EventoUso).filter(EventoUso.usuario_id == uid).all()
    assert len(restantes) == 1
    assert restantes[0].dia == date(2025, 9, 29)

    # B) REPORTE_USO_EMAILS vacío y ADMIN_ALERT_EMAIL cargado
    mock_envio.reset_mock()
    monkeypatch.setattr(settings, "REPORTE_USO_EMAILS", "")
    monkeypatch.setenv("ADMIN_ALERT_EMAIL", "c@z.com")

    res2 = _job_reporte_uso_semanal(db=db, hoy=hoy)
    assert res2 is True
    assert mock_envio.call_count == 1
    assert mock_envio.call_args[0][0] == "c@z.com"

    # C) Ambos vacíos
    mock_envio.reset_mock()
    monkeypatch.setattr(settings, "REPORTE_USO_EMAILS", "")
    monkeypatch.setenv("ADMIN_ALERT_EMAIL", "")

    res3 = _job_reporte_uso_semanal(db=db, hoy=hoy)
    assert res3 is False
    assert mock_envio.call_count == 0


def test_activacion_borde_24_horas(db: Session):
    """
    Verifica el límite exacto de 24 horas para activación.
    UA alta 27/10/2026 10:00 con transacción MANUAL el 28/10/2026 10:00 (24 horas justas: activado).
    UB alta 27/10/2026 10:00 con transacción MANUAL el 28/10/2026 16:00 (30 horas: no activado).
    """
    hoy = date(2026, 11, 2)

    # UA: alta 27/10/2026 10:00 con transacción MANUAL creada el 28/10/2026 10:00 (24 horas justas)
    ua_reg = datetime(2026, 10, 27, 10, 0, tzinfo=TZ_ARGENTINA)
    ua = _crear_usuario(db, "ua@test.com", ua_reg)
    tx_ua = Transaccion(
        id=uuid4(),
        usuario_id=ua.id,
        billetera_id=ua._billetera_test.id,
        monto=Decimal("100.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=date(2026, 10, 28),
        fecha_creacion=datetime(2026, 10, 28, 10, 0, tzinfo=TZ_ARGENTINA),
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(tx_ua)

    # UB: alta 27/10/2026 10:00 con transacción MANUAL creada el 28/10/2026 16:00 (30 horas)
    ub_reg = datetime(2026, 10, 27, 10, 0, tzinfo=TZ_ARGENTINA)
    ub = _crear_usuario(db, "ub@test.com", ub_reg)
    tx_ub = Transaccion(
        id=uuid4(),
        usuario_id=ub.id,
        billetera_id=ub._billetera_test.id,
        monto=Decimal("200.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=date(2026, 10, 28),
        fecha_creacion=datetime(2026, 10, 28, 16, 0, tzinfo=TZ_ARGENTINA),
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(tx_ub)
    db.commit()

    rep = calcular_reporte(db, hoy)
    assert rep["activacion"] == {"a": 1, "n": 2, "p": "50%"}


