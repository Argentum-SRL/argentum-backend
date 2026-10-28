# -*- coding: utf-8 -*-
"""
tests/test_wpp_deshacer_espejos.py — Pruebas de deshacer y corregir operaciones con espejos por WhatsApp.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
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
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.deshacer_corregir import _confirmar_propuesta_corregir
from app.routers.whatsapp.handlers_confirmaciones import manejar_confirmacion
from app.routers.whatsapp.handlers_deshacer import (
    manejar_corregir,
    manejar_deshacer,
)
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import que_es_plata_service, reintegro_service, transaccion_service
from app.services.conciliacion_service import calcular_saldo_teorico
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
        email="test_deshacer_espejos@argentum.com",
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
    b_mp = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        fecha_saldo=date(2026, 9, 1),
        es_principal=False,
        es_inversion=False,
    )
    db.add_all([b_galicia, b_mp])

    cat_ocio = Categoria(id=uuid4(), nombre="Ocio", tipo=TipoCategoria.EGRESO)
    cat_alim = Categoria(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO)
    cat_hogar = Categoria(id=uuid4(), nombre="Hogar", tipo=TipoCategoria.EGRESO)
    cat_otros_ing = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO)
    db.add_all([cat_ocio, cat_alim, cat_hogar, cat_otros_ing])
    db.flush()

    sub_hobbies = Subcategoria(id=uuid4(), categoria_id=cat_ocio.id, nombre="Hobbies y juegos", orden=1)
    sub_salidas = Subcategoria(id=uuid4(), categoria_id=cat_ocio.id, nombre="Salidas", orden=2)
    sub_super = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Supermercado", orden=1)
    sub_kiosco = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Kiosco", orden=2)
    sub_muebles = Subcategoria(id=uuid4(), categoria_id=cat_hogar.id, nombre="Muebles y electro", orden=1)
    sub_cobros = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
    db.add_all([sub_hobbies, sub_salidas, sub_super, sub_kiosco, sub_muebles, sub_cobros])

    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Visa",
        limite_credito=Decimal("1000000.00"),
        dia_cierre=22,
        dia_vencimiento=4,
        billetera_id=b_galicia.id,
        red=RedTarjeta.VISA,
    )
    db.add(tarjeta)
    db.commit()

    return {
        "galicia": b_galicia,
        "mp": b_mp,
        "tarjeta": tarjeta,
        "cat_alim": cat_alim,
        "sub_super": sub_super,
        "sub_kiosco": sub_kiosco,
        "cat_ocio": cat_ocio,
        "sub_salidas": sub_salidas,
        "cat_hogar": cat_hogar,
        "sub_muebles": sub_muebles,
        "cat_otros_ing": cat_otros_ing,
        "sub_cobros": sub_cobros,
    }


def test_tf_7a(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
    # Compra "Súper" de 100.000 en débito desde Galicia, hoy, en Supermercado
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
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    # ConversacionWpp con accion_ejecutada = id
    conv = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 100 mil en el súper",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=str(tx_super.id),
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv)
    db.commit()

    # reintegro de 25.000 recibido hoy
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, tx_super.id, monto=Decimal("25000.00"), recibido=True, fecha_recibido=hoy_argentina()
    )

    # manejar_deshacer("borrá eso", ...)
    ok_deshacer = manejar_deshacer("borrá eso", usuario, db, "5491112345678")
    assert ok_deshacer is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[-1] == (
        "¿Querés eliminar el último movimiento de $100.000 en Supermercado desde Galicia? "
        "También se borra su reintegro de $25.000. ¿Confirmás?"
    )

    # manejar_confirmacion("sí", ...)
    ok_conf = manejar_confirmacion("sí", usuario, db, "5491112345678")
    assert ok_conf is True
    assert mensajes_wpp[-1] == "Listo, movimiento eliminado."

    # no queda la compra ni ningún espejo
    txs_restantes = db.execute(select(Transaccion).where(Transaccion.usuario_id == usuario.id)).scalars().all()
    assert len(txs_restantes) == 0

    # saldo de Galicia 1000000.00 e igual al teórico
    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("1000000.00")
    assert calcular_saldo_teorico(db, b_galicia.id) == b_galicia.saldo_actual


def test_tf_7b(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
    # Compra "Súper" de 100.000 y reintegro de 25.000
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
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, tx_super.id, monto=Decimal("25000.00"), recibido=True, fecha_recibido=hoy_argentina()
    )

    # Ingreso "Juan" de 30.000 por transferencia en Galicia, hoy, en Cobros de personas
    tx_juan = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            categoria_id=datos_base["cat_otros_ing"].id,
            subcategoria_id=datos_base["sub_cobros"].id,
            billetera_id=b_galicia.id,
            fecha=hoy_argentina(),
            descripcion="Juan",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    # Pasado a parte de "Súper"
    que_es_plata_service.responder(
        db, usuario.id, tx_juan.id, opcion="parte_de_gasto", gasto_id=tx_super.id
    )

    # La conversación de la compra es la última
    conv = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 100 mil en el súper",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=str(tx_super.id),
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv)
    db.commit()

    ok_deshacer = manejar_deshacer("borrá eso", usuario, db, "5491112345678")
    assert ok_deshacer is True
    assert mensajes_wpp[-1] == (
        "¿Querés eliminar el último movimiento de $100.000 en Supermercado desde Galicia? "
        "También se borra su reintegro de $25.000. "
        "Los $30.000 que te devolvieron de este gasto vuelven a quedar como cobro. ¿Confirmás?"
    )

    ok_conf = manejar_confirmacion("sí", usuario, db, "5491112345678")
    assert ok_conf is True

    # Después de "sí": "Juan" es ingreso de 30000.00 en Cobros de personas sin reintegro_de_id
    db.refresh(tx_juan)
    assert tx_juan.tipo == TipoTransaccion.INGRESO
    assert tx_juan.monto == Decimal("30000.00")
    assert tx_juan.subcategoria.nombre == "Cobros de personas"
    assert tx_juan.reintegro_de_id is None

    # saldo de Galicia 1030000.00 e igual al teórico
    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("1030000.00")
    assert calcular_saldo_teorico(db, b_galicia.id) == b_galicia.saldo_actual


def test_tf_7c(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    # Compra "Tele" de 30.000 con la tarjeta Visa en 1 cuota, hoy, en Muebles y electro
    tarjeta = datos_base["tarjeta"]
    tx_tele = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.CREDITO,
            tarjeta_id=tarjeta.id,
            billetera_id=tarjeta.billetera_id,
            categoria_id=datos_base["cat_hogar"].id,
            subcategoria_id=datos_base["sub_muebles"].id,
            fecha=hoy_argentina(),
            descripcion="Tele",
            info_cuotas=InfoCuotas(cantidad_cuotas=1, monto_total=Decimal("30000.00")),
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    # reintegro de 10.000 recibido hoy
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, tx_tele.id, monto=Decimal("10000.00"), recibido=True, fecha_recibido=hoy_argentina()
    )
    conv = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="compré una tele por 30000 con visa",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=str(tx_tele.id),
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv)
    db.commit()

    ok_deshacer = manejar_deshacer("borrá eso", usuario, db, "5491112345678")
    assert ok_deshacer is True
    assert mensajes_wpp[-1] == (
        "¿Querés eliminar el último consumo de $30.000 en Muebles y electro con tarjeta Visa? "
        "También se borra su reintegro de $10.000. ¿Confirmás?"
    )


def test_espejo(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
    # Gasto "Asado" de 80.000 (web)
    tx_asado = transaccion_service.crear_transaccion(
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
    # Ingreso "Juan" de 50.000 de WhatsApp
    tx_juan = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("50000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            categoria_id=datos_base["cat_otros_ing"].id,
            subcategoria_id=datos_base["sub_cobros"].id,
            billetera_id=b_galicia.id,
            fecha=hoy_argentina(),
            descripcion="Juan",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    # Pasado a parte de Asado
    que_es_plata_service.responder(
        db, usuario.id, tx_juan.id, opcion="parte_de_gasto", gasto_id=tx_asado.id
    )

    conv_juan = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="me transfirió Juan 50000",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=str(tx_juan.id),
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv_juan)
    db.commit()

    ok_deshacer = manejar_deshacer("borrá eso", usuario, db, "5491112345678")
    assert ok_deshacer is True
    assert mensajes_wpp[-1] == "¿Querés eliminar la devolución de $50.000 de «Asado» en Galicia? ¿Confirmás?"

    ok_corr = manejar_corregir("no, eran 40000", usuario, db, "5491112345678")
    assert ok_corr is True
    assert mensajes_wpp[-1] == "Ese movimiento es una devolución de un gasto. Para cambiarlo, entrá a la web de Argentum."

    db.refresh(tx_juan)
    assert tx_juan.monto == Decimal("-50000.00")


def test_corregir_con_reintegro(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
    b_mp = datos_base["mp"]

    # Compra "Súper" de WhatsApp con reintegro de 25.000
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
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, tx_super.id, monto=Decimal("25000.00"), recibido=True, fecha_recibido=hoy_argentina()
    )
    conv = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 100 mil en súper con galicia",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=str(tx_super.id),
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv)
    db.commit()

    ok_corr = manejar_corregir("no, fue con mercado pago", usuario, db, "5491112345678")
    assert ok_corr is True
    assert mensajes_wpp[-1] == "Primero quitá el reintegro para cambiar el medio de pago o la billetera."
    db.refresh(tx_super)
    assert tx_super.billetera_id == b_galicia.id

    # ConversacionWpp intent "corregir", accion None, con entidades {"transaccion_id": id, "cambios": {"billetera_id": id_mp}}
    conv_corr_pendiente = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="no, fue con mercado pago",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="corregir",
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        fecha=datetime.now(timezone.utc),
        entidades={
            "transaccion_id": str(tx_super.id),
            "cambios": {"billetera_id": str(b_mp.id)},
        },
    )
    db.add(conv_corr_pendiente)
    db.commit()

    tx_res, msg_res, ya_c = _confirmar_propuesta_corregir(usuario, db)
    assert tx_res is None
    assert msg_res == "Primero quitá el reintegro para cambiar el medio de pago o la billetera."
    assert ya_c is False
    assert conv_corr_pendiente.accion_ejecutada == "error"
    db.refresh(tx_super)
    assert tx_super.billetera_id == b_galicia.id


def test_sin_espejos(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
    tx_kiosco = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.DEBITO,
            categoria_id=datos_base["cat_alim"].id,
            subcategoria_id=datos_base["sub_kiosco"].id,
            billetera_id=b_galicia.id,
            fecha=hoy_argentina(),
            descripcion="Kiosco",
            origen=OrigenTransaccion.IA_WPP,
        ),
    )
    conv = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 5000 en el kiosco",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=str(tx_kiosco.id),
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv)
    db.commit()

    ok = manejar_deshacer("borrá eso", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[-1] == "¿Querés eliminar el último movimiento de $5.000 en Kiosco desde Galicia? ¿Confirmás?"

