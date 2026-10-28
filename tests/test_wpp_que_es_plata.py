# -*- coding: utf-8 -*-
"""
tests/test_wpp_que_es_plata.py — Tests de "¿Qué es esta plata?" y respuestas numeradas en WhatsApp.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.aviso_personas_wpp import (
    TEXTO_AVISO_COBROS,
    TEXTO_PREGUNTA_COBRO,
    agregar_aviso_personas,
)
from app.routers.whatsapp.que_es_plata_wpp import manejar_respuesta_numerada
from app.schemas.transaccion import TransaccionCreate
from app.services import transaccion_service
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.whatsapp_service import BuzonSalida, _buzon_actual_cv
from app.utils.fecha import hoy_argentina


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
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

    from sqlalchemy.dialects.sqlite.base import DATETIME
    orig_dt_proc = DATETIME.result_processor
    def _safe_dt_proc(self, dialect, coltype):
        proc = orig_dt_proc(self, dialect, coltype)
        def process(value):
            res = proc(value) if proc else value
            if res is not None and isinstance(res, datetime) and res.tzinfo is None:
                return res.replace(tzinfo=timezone.utc)
            return res
        return process
    monkeypatch.setattr(DATETIME, "result_processor", _safe_dt_proc)

    yield


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        def _sqlite_translate(val, from_chars, to_chars):
            if val is None:
                return None
            return str(val).translate(str.maketrans(from_chars, to_chars))
        dbapi_connection.create_function("translate", 3, _sqlite_translate)

    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(name="usuario")
def usuario_fixture(db: Session):
    user = Usuario(
        id=uuid4(),
        email="test_que_es_plata_wpp@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="mensajes_wpp")
def mensajes_wpp_fixture(monkeypatch):
    mensajes = []

    def mock_enviar(to, text, **kwargs):
        mensajes.append(text)
        return True

    monkeypatch.setattr("app.services.whatsapp_service.enviar_whatsapp", mock_enviar)
    return mensajes


@pytest.fixture(name="datos_base")
def datos_base_fixture(db: Session, usuario: Usuario):
    b_galicia = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        fecha_saldo=date(2026, 9, 1),
        es_principal=True,
        es_inversion=False,
    )
    db.add(b_galicia)

    cat_alim = Categoria(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO)
    cat_ocio = Categoria(id=uuid4(), nombre="Ocio", tipo=TipoCategoria.EGRESO)
    cat_otros_egr = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    cat_otros_ing = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO)
    db.add_all([cat_alim, cat_ocio, cat_otros_egr, cat_otros_ing])
    db.flush()

    sub_super = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Supermercado", orden=1)
    sub_salidas = Subcategoria(id=uuid4(), categoria_id=cat_ocio.id, nombre="Salidas", orden=1)
    sub_cobros = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
    sub_paso_ing = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Pasó por mi cuenta", orden=2)
    sub_paso_egr = Subcategoria(id=uuid4(), categoria_id=cat_otros_egr.id, nombre="Pasó por mi cuenta", orden=1)
    db.add_all([sub_super, sub_salidas, sub_cobros, sub_paso_ing, sub_paso_egr])
    db.commit()

    return {
        "galicia": b_galicia,
        "cat_alim": cat_alim,
        "sub_super": sub_super,
        "cat_ocio": cat_ocio,
        "sub_salidas": sub_salidas,
        "cat_otros_ing": cat_otros_ing,
        "sub_cobros": sub_cobros,
        "sub_paso_ing": sub_paso_ing,
    }


def _crear_cobro_juan(db: Session, usuario: Usuario, datos_base) -> tuple[Transaccion, datetime]:
    dt_inicio = datetime.now(timezone.utc) - timedelta(seconds=1)
    tx_juan = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            billetera_id=datos_base["galicia"].id,
            categoria_id=datos_base["cat_otros_ing"].id,
            subcategoria_id=datos_base["sub_cobros"].id,
            fecha=hoy_argentina(),
            descripcion="Juan",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    return tx_juan, dt_inicio


def test_aviso(db: Session, usuario: Usuario, datos_base):
    # Caso 1: Un cobro
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)
    buzon1 = BuzonSalida("5491112345678")
    token1 = _buzon_actual_cv.set(buzon1)
    try:
        ok1 = agregar_aviso_personas(db, usuario, dt_inicio)
        assert ok1 is True
        assert buzon1._aviso_texto == TEXTO_PREGUNTA_COBRO
        assert len(TEXTO_PREGUNTA_COBRO) <= 90
        assert "\n" not in TEXTO_PREGUNTA_COBRO

        convs = db.execute(
            select(ConversacionWpp).where(ConversacionWpp.intent_detectado == "que_es_esta_plata")
        ).scalars().all()
        assert len(convs) == 1
        assert (convs[0].entidades or {}).get("transaccion_id") == str(tx_juan.id)
    finally:
        _buzon_actual_cv.reset(token1)

    # Limpiar conversaciones
    db.query(ConversacionWpp).delete()
    db.commit()

    # Caso 2: Con dos cobros
    dt_inicio2 = datetime.now(timezone.utc) - timedelta(seconds=1)
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("15000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            billetera_id=datos_base["galicia"].id,
            categoria_id=datos_base["cat_otros_ing"].id,
            subcategoria_id=datos_base["sub_cobros"].id,
            fecha=hoy_argentina(),
            descripcion="Pedro",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    buzon2 = BuzonSalida("5491112345678")
    token2 = _buzon_actual_cv.set(buzon2)
    try:
        ok2 = agregar_aviso_personas(db, usuario, dt_inicio2)
        assert ok2 is True
        assert buzon2._aviso_texto == TEXTO_AVISO_COBROS
        convs2 = db.execute(
            select(ConversacionWpp).where(ConversacionWpp.intent_detectado == "que_es_esta_plata")
        ).scalars().all()
        assert len(convs2) == 0
    finally:
        _buzon_actual_cv.reset(token2)

    # Caso 3: Con recordatorio de impago ya puesto en el buzón
    buzon3 = BuzonSalida("5491112345678")
    buzon3.agregar_aviso("Tenés el resumen por vencer.", "recordatorio_impago")
    token3 = _buzon_actual_cv.set(buzon3)
    try:
        ok3 = agregar_aviso_personas(db, usuario, dt_inicio)
        assert ok3 is False
        assert buzon3._aviso_texto == "Tenés el resumen por vencer."
    finally:
        _buzon_actual_cv.reset(token3)


def test_1(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)
    buzon = BuzonSalida("5491112345678")
    token = _buzon_actual_cv.set(buzon)
    try:
        agregar_aviso_personas(db, usuario, dt_inicio)
    finally:
        _buzon_actual_cv.reset(token)

    mensajes_wpp.clear()
    ok = manejar_respuesta_numerada("1", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "Listo, queda como cobro."

    db.refresh(tx_juan)
    assert tx_juan.tipo == TipoTransaccion.INGRESO
    assert tx_juan.subcategoria.nombre == "Cobros de personas"

    conv_estado = db.execute(
        select(ConversacionWpp).where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.mensaje_usuario == "",
        )
    ).scalars().first()
    assert conv_estado.accion_ejecutada == "respondida"


def test_3(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)
    buzon = BuzonSalida("5491112345678")
    token = _buzon_actual_cv.set(buzon)
    try:
        agregar_aviso_personas(db, usuario, dt_inicio)
    finally:
        _buzon_actual_cv.reset(token)

    mensajes_wpp.clear()
    ok = manejar_respuesta_numerada("3", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "Listo, queda como plata que solo pasó por tu cuenta: no cuenta como ingreso."

    db.refresh(tx_juan)
    assert tx_juan.subcategoria.nombre == "Pasó por mi cuenta"


def test_2(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
    ayer = hoy_argentina() - timedelta(days=1)
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("80000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.DEBITO,
            categoria_id=datos_base["cat_ocio"].id,
            subcategoria_id=datos_base["sub_salidas"].id,
            billetera_id=b_galicia.id,
            fecha=hoy_argentina(),
            descripcion="Asado",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    tx_super = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.DEBITO,
            categoria_id=datos_base["cat_alim"].id,
            subcategoria_id=datos_base["sub_super"].id,
            billetera_id=b_galicia.id,
            fecha=ayer,
            descripcion="Súper",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)
    buzon = BuzonSalida("5491112345678")
    token = _buzon_actual_cv.set(buzon)
    try:
        agregar_aviso_personas(db, usuario, dt_inicio)
    finally:
        _buzon_actual_cv.reset(token)

    db.refresh(b_galicia)
    saldo_antes_respuestas = b_galicia.saldo_actual

    mensajes_wpp.clear()
    ok_2 = manejar_respuesta_numerada("2", usuario, db, "5491112345678")
    assert ok_2 is True
    assert mensajes_wpp[0] == "¿De qué gasto?\n1) Asado $80.000\n2) Súper $100.000 (ayer)\nRespondé con el número."

    mensajes_wpp.clear()
    ok_elegir = manejar_respuesta_numerada("2", usuario, db, "5491112345678")
    assert ok_elegir is True
    assert mensajes_wpp[0] == "Listo, los $30.000 se restan de «Súper»."

    db.refresh(tx_juan)
    assert tx_juan.reintegro_de_id == tx_super.id
    assert tx_juan.tipo_espejo == "parte"
    assert tx_juan.monto == Decimal("-30000.00")

    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == saldo_antes_respuestas
    assert b_galicia.saldo_actual == calcular_saldo_teorico(db, b_galicia.id)


def test_2_sin_gastos(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)
    buzon = BuzonSalida("5491112345678")
    token = _buzon_actual_cv.set(buzon)
    try:
        agregar_aviso_personas(db, usuario, dt_inicio)
    finally:
        _buzon_actual_cv.reset(token)

    mensajes_wpp.clear()
    ok = manejar_respuesta_numerada("2", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "No encontré un gasto para vincularla. Podés hacerlo desde la web de Argentum."


def test_fuera_de_rango(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)
    buzon = BuzonSalida("5491112345678")
    token = _buzon_actual_cv.set(buzon)
    try:
        agregar_aviso_personas(db, usuario, dt_inicio)
    finally:
        _buzon_actual_cv.reset(token)

    assert manejar_respuesta_numerada("4", usuario, db, "5491112345678") is False


def test_vencido(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)
    buzon = BuzonSalida("5491112345678")
    token = _buzon_actual_cv.set(buzon)
    try:
        agregar_aviso_personas(db, usuario, dt_inicio)
    finally:
        _buzon_actual_cv.reset(token)

    # Modificar fecha del estado a 16 minutos atrás
    estado = db.execute(
        select(ConversacionWpp).where(ConversacionWpp.intent_detectado == "que_es_esta_plata")
    ).scalars().first()
    estado.fecha = datetime.now(timezone.utc) - timedelta(minutes=16)
    db.commit()

    assert manejar_respuesta_numerada("1", usuario, db, "5491112345678") is False


def test_cruce_con_dolares(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    tx_juan, dt_inicio = _crear_cobro_juan(db, usuario, datos_base)

    # Caso A: estado "que_es_esta_plata" y DESPUÉS una propuesta "propuesta_usd_pesos"
    ahora = datetime.now(timezone.utc)
    conv_plata = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot=TEXTO_PREGUNTA_COBRO,
        intent_detectado="que_es_esta_plata",
        entidades={"transaccion_id": str(tx_juan.id)},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
        fecha=ahora - timedelta(seconds=10),
    )
    conv_dolares = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 20 dólares en steam con débito",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="Voy a anotar US$20...",
        intent_detectado="registrar_transaccion",
        entidades={
            "tipo_flujo": "propuesta_usd_pesos",
            "monto": 26000.0,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": "Hobbies y juegos",
            "billetera": "Galicia",
            "billetera_origen": "Galicia",
            "moneda_original": "USD",
            "monto_original": 20.0,
            "cotizacion_sugerida": 1000.0,
            "percepcion": 30,
            "monto_editado": False,
        },
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
        fecha=ahora,
    )
    db.add_all([conv_plata, conv_dolares])
    db.commit()

    # "1" -> manejar_respuesta_numerada False y Juan sin cambios
    res_a = manejar_respuesta_numerada("1", usuario, db, "5491112345678")
    assert res_a is False
    db.refresh(tx_juan)
    assert tx_juan.subcategoria.nombre == "Cobros de personas"

    # Caso B: Al revés (propuesta primero, estado después)
    db.query(ConversacionWpp).delete()
    db.commit()

    conv_dolares_b = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 20 dólares en steam con débito",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="Voy a anotar US$20...",
        intent_detectado="registrar_transaccion",
        entidades={
            "tipo_flujo": "propuesta_usd_pesos",
            "monto": 26000.0,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": "Hobbies y juegos",
            "billetera": "Galicia",
            "billetera_origen": "Galicia",
            "moneda_original": "USD",
            "monto_original": 20.0,
            "cotizacion_sugerida": 1000.0,
            "percepcion": 30,
            "monto_editado": False,
        },
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
        fecha=ahora - timedelta(seconds=10),
    )
    conv_plata_b = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot=TEXTO_PREGUNTA_COBRO,
        intent_detectado="que_es_esta_plata",
        entidades={"transaccion_id": str(tx_juan.id)},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
        fecha=ahora,
    )
    db.add_all([conv_dolares_b, conv_plata_b])
    db.commit()

    mensajes_wpp.clear()
    res_b = manejar_respuesta_numerada("1", usuario, db, "5491112345678")
    assert res_b is True
    assert mensajes_wpp[0] == "Listo, queda como cobro."
    db.refresh(conv_dolares_b)
    assert conv_dolares_b.entidades["monto"] == 26000.0

