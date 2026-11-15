# -*- coding: utf-8 -*-
"""
tests/test_transferencias_m1_flujo.py — Casos F1 a F39 de transferencias M1.
"""
from __future__ import annotations

import time
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
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.detectors import _bloquear_mezcla_en_handlers
from app.routers.whatsapp.etapa_resolucion import procesar_resolucion_billetera_y_propuestas
from app.routers.whatsapp.handlers_confirmaciones import (
    _es_confirmacion,
    manejar_confirmacion,
)
from app.routers.whatsapp.handlers_deshacer import (
    _es_pedido_deshacer,
    manejar_deshacer,
)
from app.routers.whatsapp.handlers_transferencias import manejar_transferencias
from app.routers.whatsapp.transferencias_flujo import manejar_transferencia_ia
from app.routers.whatsapp.db_lookups import _buscar_slot_filling_activo
from app.routers.whatsapp_ia import MSG_NO_MEZCLAR_TRANSFERENCIAS


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

    f_hoy = date(2026, 11, 14)
    import app.utils.fecha
    monkeypatch.setattr(app.utils.fecha, "hoy_argentina", lambda: f_hoy)
    modulos = [
        "app.routers.whatsapp.transferencias_flujo",
        "app.routers.whatsapp.transferencias_flujo_b",
        "app.routers.whatsapp.handlers_transferencias",
        "app.routers.whatsapp.detectors",
        "app.routers.whatsapp.transferencias",
        "app.routers.whatsapp.etapa_resolucion",
        "app.routers.whatsapp.handlers_confirmaciones",
        "app.routers.whatsapp.handlers_deshacer",
        "app.routers.whatsapp.deshacer_corregir",
        "app.services.transferencia_service",
        "app.services.saldo_billetera_service",
        "app.routers.whatsapp.db_lookups",
        "app.routers.whatsapp.etapa_handlers",
        "app.routers.whatsapp.registro",
        "app.routers.whatsapp.parsers",
    ]
    import sys
    for mod_name in modulos:
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "hoy_argentina"):
            monkeypatch.setattr(mod, "hoy_argentina", lambda: f_hoy)

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
        email="test_flujo_m1@argentum.com",
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
    f_saldo = date(2026, 10, 1)

    b_galicia = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        fecha_saldo=f_saldo,
        tiene_punto_partida=True,
        es_principal=True,
        es_inversion=False,
        es_efectivo=False,
        entidad_id="galicia",
        estado=EstadoBilletera.ACTIVA,
    )
    b_santander = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Santander",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        fecha_saldo=f_saldo,
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=False,
        es_efectivo=False,
        entidad_id="santander",
        estado=EstadoBilletera.ACTIVA,
    )
    b_mp = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("5000.00"),
        saldo_actual=Decimal("5000.00"),
        fecha_saldo=f_saldo,
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=False,
        es_efectivo=False,
        entidad_id="mercadopago",
        estado=EstadoBilletera.ACTIVA,
    )
    b_efectivo_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Efectivo Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("10000.00"),
        saldo_actual=Decimal("10000.00"),
        fecha_saldo=f_saldo,
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=False,
        es_efectivo=True,
        entidad_id=None,
        estado=EstadoBilletera.ACTIVA,
    )
    b_efectivo_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Efectivo Dólares",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("500.00"),
        saldo_actual=Decimal("500.00"),
        fecha_saldo=f_saldo,
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=False,
        es_efectivo=True,
        entidad_id=None,
        estado=EstadoBilletera.ACTIVA,
    )
    b_ahorro = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Ahorro",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("200000.00"),
        saldo_actual=Decimal("200000.00"),
        fecha_saldo=f_saldo,
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=True,
        es_efectivo=False,
        entidad_id=None,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([
        b_galicia, b_santander, b_mp,
        b_efectivo_ars, b_efectivo_usd, b_ahorro,
    ])

    cat_otros = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    cat_banco = Categoria(id=uuid4(), nombre="Banco", tipo=TipoCategoria.EGRESO)
    db.add_all([cat_otros, cat_banco])
    db.flush()

    sub_pagos = Subcategoria(
        id=uuid4(),
        categoria_id=cat_otros.id,
        nombre="Pagos a personas",
        orden=1,
    )
    sub_comisiones = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Comisiones y gastos bancarios",
        orden=1,
    )
    db.add_all([sub_pagos, sub_comisiones])
    db.commit()

    return {
        "galicia": b_galicia,
        "santander": b_santander,
        "mp": b_mp,
        "efectivo_ars": b_efectivo_ars,
        "efectivo_usd": b_efectivo_usd,
        "ahorro": b_ahorro,
        "cat_otros": cat_otros,
        "sub_pagos": sub_pagos,
        "cat_banco": cat_banco,
        "sub_comisiones": sub_comisiones,
    }


def simular_mensaje(
    texto: str,
    usuario: Usuario,
    db: Session,
    numero: str = "5491100000000",
) -> bool:
    wamid = f"wamid_{uuid4().hex[:8]}"
    if _es_pedido_deshacer(texto):
        return manejar_deshacer(texto, usuario, db, numero, wamid=wamid)
    if _es_confirmacion(texto):
        return manejar_confirmacion(texto, usuario, db, numero, wamid=wamid)
    conv_activa = _buscar_slot_filling_activo(usuario.id, db)
    estado = conv_activa.slot_filling_estado if conv_activa else None
    return manejar_transferencias(
        texto,
        usuario,
        db,
        numero,
        wamid=wamid,
        conv_activa=conv_activa,
        estado_previo=estado,
    )


def test_f1_directa_palabras(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20 lucas de galicia a santander", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste $20.000 de Galicia a Santander."

    b_g = datos_base["galicia"]
    b_s = datos_base["santander"]
    db.refresh(b_g)
    db.refresh(b_s)
    assert b_g.saldo_actual == Decimal("80000.00")
    assert b_s.saldo_actual == Decimal("70000.00")

    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 1
    assert trs[0].fecha == date(2026, 11, 14)

    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 0

    conv = db.execute(
        select(ConversacionWpp).order_by(ConversacionWpp.fecha.desc())
    ).scalars().first()
    assert conv.accion_ejecutada == f"transferencia:{trs[0].id}"


def test_f2_directa_orden_invertido(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("transferí 20k a santander desde galicia", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste $20.000 de Galicia a Santander."

    b_g = datos_base["galicia"]
    b_s = datos_base["santander"]
    db.refresh(b_g)
    db.refresh(b_s)
    assert b_g.saldo_actual == Decimal("80000.00")
    assert b_s.saldo_actual == Decimal("70000.00")


def test_f3_pregunta_origen_y_overdraft(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("pasé 20k a santander", usuario, db)
    assert res1 is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "¿Desde qué cuenta pasaste los $20.000 a Santander?\n\n"
        "1. Galicia\n"
        "2. Efectivo Pesos\n"
        "3. Mercado Pago"
    )

    res2 = simular_mensaje("3", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == (
        "Listo. Pasaste $20.000 de Mercado Pago a Santander.\n"
        "Mercado Pago quedó en negativo."
    )

    b_mp = datos_base["mp"]
    db.refresh(b_mp)
    assert b_mp.saldo_actual == Decimal("-15000.00")


def test_f4_cajero_pregunta_banco(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("saqué 10k del cajero", usuario, db)
    assert res1 is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "¿De qué cuenta sacaste los $10.000?\n\n"
        "1. Galicia\n"
        "2. Mercado Pago\n"
        "3. Santander"
    )

    res2 = simular_mensaje("galicia", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == "Listo. Pasaste $10.000 de Galicia a Efectivo Pesos."


def test_f5_cajero_banco_especificado(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("saqué 10k del galicia", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste $10.000 de Galicia a Efectivo Pesos."


def test_f6_deposito_directo(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("deposité 5k en santander", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste $5.000 de Efectivo Pesos a Santander."


def test_f7_cuenta_no_cargada_destino_crear(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("pasé 20k de galicia a macro", usuario, db)
    assert res1 is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "No tenés Macro cargada.\n"
        "1. Crearla\n"
        "2. Era otra de tus cuentas\n"
        "3. Fue un pago a otra persona"
    )

    res2 = simular_mensaje("1", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == "Listo. Creé Macro y pasaste $20.000 de Galicia a Macro."

    b_macro = db.execute(
        select(Billetera).where(Billetera.nombre == "Macro")
    ).scalar_one_or_none()
    assert b_macro is not None
    assert b_macro.moneda == Moneda.ARS
    assert b_macro.saldo_actual == Decimal("20000.00")
    assert b_macro.saldo_inicial == Decimal("0.00")
    assert b_macro.tiene_punto_partida is False
    assert b_macro.entidad_id == "macro"
    assert b_macro.es_inversion is False

    b_g = datos_base["galicia"]
    db.refresh(b_g)
    assert b_g.saldo_actual == Decimal("80000.00")


def test_f8_cuenta_no_cargada_era_otra(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("pasé 20k de galicia a macro", usuario, db)
    assert res1 is True

    res2 = simular_mensaje("2", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == (
        "¿A qué cuenta pasaste los $20.000 de Galicia?\n\n"
        "1. Efectivo Pesos\n"
        "2. Mercado Pago\n"
        "3. Santander"
    )

    res3 = simular_mensaje("3", usuario, db)
    assert res3 is True
    assert len(mensajes_wpp) == 3
    assert mensajes_wpp[2] == "Listo. Pasaste $20.000 de Galicia a Santander."

    b_macro = db.execute(
        select(Billetera).where(Billetera.nombre == "Macro")
    ).scalar_one_or_none()
    assert b_macro is None


def test_f9_cuenta_no_cargada_pago_persona_y_deshacer(
    db, usuario, datos_base, mensajes_wpp
):
    res1 = simular_mensaje("pasé 20k de galicia a macro", usuario, db)
    assert res1 is True

    res2 = simular_mensaje("3", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == (
        "Listo. $20.000 en Pagos a personas desde Galicia — registrado.\n"
        "Si te acordás para qué era, decime «era el súper» y lo cambio."
    )

    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 1
    assert txs[0].monto == Decimal("20000.00")

    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 0

    b_g = datos_base["galicia"]
    db.refresh(b_g)
    assert b_g.saldo_actual == Decimal("80000.00")

    res3 = simular_mensaje("borrá eso", usuario, db)
    assert res3 is True
    assert len(mensajes_wpp) == 3
    assert mensajes_wpp[2] == (
        "¿Querés eliminar el último movimiento de $20.000 en "
        "Pagos a personas desde Galicia? ¿Confirmás?"
    )


def test_f10_origen_no_cargado_opcion_invalida(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("pasé 20k de macro a galicia", usuario, db)
    assert res1 is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "No tenés Macro cargada.\n"
        "1. Crearla\n"
        "2. Era otra de tus cuentas"
    )

    res2 = simular_mensaje("3", usuario, db)
    assert res2 is False
    assert len(mensajes_wpp) == 1

    b_macro = db.execute(
        select(Billetera).where(Billetera.nombre == "Macro")
    ).scalar_one_or_none()
    assert b_macro is None


def test_f11_fecha_ayer(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20k de galicia a santander ayer", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste $20.000 de Galicia a Santander (ayer)."

    tr = db.execute(select(TransferenciaInterna)).scalar_one()
    assert tr.fecha == date(2026, 11, 13)


def test_f12_fecha_el_lunes(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20k de galicia a santander el lunes", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste $20.000 de Galicia a Santander (el 9 de noviembre)."


def test_f13_fecha_mas_de_60_dias(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20k de galicia a santander el 10/09", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Listo. Pasaste $20.000 de Galicia a Santander.\n"
        "No puedo registrar movimientos de más de 60 días atrás. Va a quedar con fecha de hoy."
    )

    tr = db.execute(select(TransferenciaInterna)).scalar_one()
    assert tr.fecha == date(2026, 11, 14)


def test_f14_fecha_futura(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20k de galicia a santander el 20/11", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Listo. Pasaste $20.000 de Galicia a Santander.\n"
        "No puedo registrar movimientos con fecha futura porque "
        "todavía no ocurrieron. Va a quedar con fecha de hoy."
    )


def test_f15_aviso_anterior_al_saldo(db, usuario, datos_base, mensajes_wpp):
    b_s = datos_base["santander"]
    b_s.fecha_saldo = date(2026, 11, 14)
    db.commit()

    res = simular_mensaje("pasé 20k de galicia a santander ayer", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Listo. Pasaste $20.000 de Galicia a Santander (ayer).\n"
        "Ojo: es anterior a tu saldo de «Santander» (14/11) y no lo modifica."
    )

    b_g = datos_base["galicia"]
    db.refresh(b_g)
    db.refresh(b_s)
    assert b_g.saldo_actual == Decimal("80000.00")
    assert b_s.saldo_actual == Decimal("50000.00")


def test_f16_multiples_transferencias_y_deshacer(db, usuario, datos_base, mensajes_wpp):
    msg = "pasé 20k de galicia a santander y 5k de santander a mercado pago"
    res1 = simular_mensaje(msg, usuario, db)
    assert res1 is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Listo. Pasaste $20.000 de Galicia a Santander y $5.000 de Santander a Mercado Pago."
    )

    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 2

    conv = db.execute(
        select(ConversacionWpp).order_by(ConversacionWpp.fecha.desc())
    ).scalars().first()
    assert conv.accion_ejecutada.startswith("transferencias:")

    res2 = simular_mensaje("borrá eso", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == (
        "¿Querés anular las 2 transferencias que registraste recién? ¿Confirmás?"
    )

    res3 = simular_mensaje("sí", usuario, db)
    assert res3 is True
    assert len(mensajes_wpp) == 3
    assert mensajes_wpp[2] == "Listo, 2 transferencias anuladas."

    trs_post = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs_post) == 0

    b_g = datos_base["galicia"]
    b_s = datos_base["santander"]
    b_mp = datos_base["mp"]
    db.refresh(b_g)
    db.refresh(b_s)
    db.refresh(b_mp)
    assert b_g.saldo_actual == Decimal("100000.00")
    assert b_s.saldo_actual == Decimal("50000.00")
    assert b_mp.saldo_actual == Decimal("5000.00")


def test_f17_multiples_incompletas(db, usuario, datos_base, mensajes_wpp):
    msg = "pasé 20k de galicia a santander y 5k a mercado pago"
    res = simular_mensaje(msg, usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "No pude entender todas las transferencias. Mandame cada una en un mensaje aparte."
    )

    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 0


def test_f18_distinta_moneda_pregunta_cotizacion(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 100 dólares a galicia", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "¿A qué cotización vendiste o cuántos pesos recibís en Galicia?"

    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 0

    conv = db.execute(
        select(ConversacionWpp).order_by(ConversacionWpp.fecha.desc())
    ).scalars().first()
    assert conv.slot_filling_estado["tipo_operacion"] == "venta_usd"
    b_ef_usd = datos_base["efectivo_usd"]
    b_g = datos_base["galicia"]
    assert conv.slot_filling_estado["billetera_origen_id"] == str(b_ef_usd.id)
    assert conv.slot_filling_estado["billetera_destino_id"] == str(b_g.id)


def test_f18b_distinta_moneda_con_cuenta_usd_existente(
    db, usuario, datos_base, mensajes_wpp
):
    b_galicia_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia USD",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=False,
        es_efectivo=False,
        entidad_id="galicia",
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_galicia_usd)
    db.commit()

    res = simular_mensaje("pasé 100 dólares a galicia", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste US$100 de Efectivo Dólares a Galicia USD."


def test_f19_misma_cuenta(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 10 mil de galicia a galicia", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "La billetera de origen y destino no pueden ser la misma."


def test_f20_le_pase_no_transferencia(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("le pasé 20k a santander", usuario, db)
    assert res is False
    assert len(mensajes_wpp) == 0


def test_f21_cuenta_desconocida_deterministico(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20k a juan", usuario, db)
    assert res is False
    assert len(mensajes_wpp) == 0


def test_f22_overdraft_directo(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 300k de galicia a santander", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Listo. Pasaste $300.000 de Galicia a Santander.\n"
        "Galicia quedó en negativo."
    )

    b_g = datos_base["galicia"]
    db.refresh(b_g)
    assert b_g.saldo_actual == Decimal("-200000.00")


def test_f23_extraccion_con_comision(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("saqué 10k de galicia con 500 de comisión", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Listo. Pasaste $10.000 de Galicia a Efectivo Pesos, con $500 de comisión."
    )

    b_g = datos_base["galicia"]
    b_ef = datos_base["efectivo_ars"]
    db.refresh(b_g)
    db.refresh(b_ef)
    assert b_g.saldo_actual == Decimal("89500.00")
    assert b_ef.saldo_actual == Decimal("20000.00")

    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 1
    assert txs[0].monto == Decimal("500.00")


def test_f24_confirmacion_tras_directo_y_deshacer(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("pasé 20k de galicia a santander", usuario, db)
    assert res1 is True

    res2 = simular_mensaje("sí", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == "Ya quedó anotado. Si hay algo mal, decime qué corregir."

    conv = db.execute(
        select(ConversacionWpp).order_by(ConversacionWpp.fecha.desc())
    ).scalars().first()
    assert conv.accion_ejecutada.startswith("transferencia:")

    res3 = simular_mensaje("deshacer", usuario, db)
    assert res3 is True
    assert len(mensajes_wpp) == 3
    assert mensajes_wpp[2] == (
        "¿Querés anular la transferencia de $20.000 de Galicia a Santander? ¿Confirmás?"
    )

    res4 = simular_mensaje("sí", usuario, db)
    assert res4 is True
    assert len(mensajes_wpp) == 4
    assert mensajes_wpp[3] == "Listo, movimiento eliminado."

    b_g = datos_base["galicia"]
    b_s = datos_base["santander"]
    db.refresh(b_g)
    db.refresh(b_s)
    assert b_g.saldo_actual == Decimal("100000.00")
    assert b_s.saldo_actual == Decimal("50000.00")


def test_f25_camino_ia_propuesta_y_confirmacion(db, usuario, datos_base, mensajes_wpp):
    entidades = {
        "billetera_origen": "Galicia",
        "billetera_destino": "Santander",
        "monto": 20000,
        "moneda": "ARS",
        "fecha": "2026-11-13",
    }
    manejar_transferencia_ia(
        entidades,
        "transferi 20k a santander ayer",
        usuario,
        db,
        "5491100000000",
        "wamid_ia_25",
    )
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Voy a transferir $20.000 de Galicia a Santander (ayer). ¿Confirmás?"
    )

    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 0

    res2 = simular_mensaje("sí", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == "Listo. Pasaste $20.000 de Galicia a Santander (ayer)."

    tr = db.execute(select(TransferenciaInterna)).scalar_one()
    assert tr.fecha == date(2026, 11, 13)


def test_f26_camino_ia_cuenta_no_cargada_destino(db, usuario, datos_base, mensajes_wpp):
    entidades = {
        "billetera_origen": "Galicia",
        "billetera_destino": "Macro",
        "monto": 20000,
    }
    manejar_transferencia_ia(
        entidades,
        "pase 20k a macro",
        usuario,
        db,
        "5491100000000",
        "wamid_ia_26",
    )
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "No tenés Macro cargada.\n"
        "1. Crearla\n"
        "2. Era otra de tus cuentas\n"
        "3. Fue un pago a otra persona"
    )

    res2 = simular_mensaje("1", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == "Creé Macro. Voy a transferir $20.000 de Galicia a Macro. ¿Confirmás?"

    res3 = simular_mensaje("sí", usuario, db)
    assert res3 is True
    assert len(mensajes_wpp) == 3
    assert mensajes_wpp[2] == "Listo. Pasaste $20.000 de Galicia a Macro."


def test_f27_camino_ia_cuenta_desconocida(db, usuario, datos_base, mensajes_wpp):
    entidades = {
        "billetera_origen": "Galicia",
        "billetera_destino": "Juan",
        "monto": 20000,
    }
    manejar_transferencia_ia(
        entidades,
        "pase 20k a juan",
        usuario,
        db,
        "5491100000000",
        "wamid_ia_27",
    )
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "No encontré la cuenta «Juan». Mandámelo así: «pasé 20 mil de Galicia a Mercado Pago»."
    )

    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 0


def test_f28_etapa_resolucion_desvio_transferir_fondos(
    db, usuario, datos_base, mensajes_wpp
):
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={},
        wamid="wamid_ctx_28",
        from_number="5491100000000",
        msg_type="text",
        t_inicio=time.time(),
        db=db,
        usuario=usuario,
        mensaje_texto="transferi 20k a santander ayer",
        resultado_ia={
            "intent": "transferir_fondos",
            "entidades": {
                "billetera_origen": "Galicia",
                "billetera_destino": "Santander",
                "monto": 20000,
                "moneda": "ARS",
                "fecha": "2026-11-13",
            },
            "confianza": 0.9,
        },
    )
    procesar_resolucion_billetera_y_propuestas(ctx)
    assert ctx.terminado is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Voy a transferir $20.000 de Galicia a Santander (ayer). ¿Confirmás?"
    )
    for m in mensajes_wpp:
        assert "tarjeta" not in m.lower()


def test_f29_monedas_pesos_con_dolares_escrito(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 100 dólares de galicia a santander", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Esas cuentas son en pesos. Mandame el monto en pesos."


def test_f30_camino_ia_sin_cuentas(db, usuario, datos_base, mensajes_wpp):
    entidades = {"monto": 20000}
    manejar_transferencia_ia(
        entidades,
        "transferi plata",
        usuario,
        db,
        "5491100000000",
        "wamid_ia_30",
    )
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Decime de qué cuenta a qué cuenta. Por ejemplo: «pasé 20 mil de Galicia a Mercado Pago»."
    )


def test_f31_alias_bna_ambigua(db, usuario, datos_base, mensajes_wpp):
    b_bna1 = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco Nación",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=False,
        es_efectivo=False,
        entidad_id="nacion",
        estado=EstadoBilletera.ACTIVA,
    )
    b_bna2 = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Nación Ahorro",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        fecha_saldo=date(2026, 10, 1),
        tiene_punto_partida=True,
        es_principal=False,
        es_inversion=False,
        es_efectivo=False,
        entidad_id="nacion",
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_bna1, b_bna2])
    db.commit()

    res = simular_mensaje("pasé 20k de bna a galicia", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "¿Desde qué cuenta pasaste los $20.000 a Galicia?\n\n"
        "1. Banco Nación\n"
        "2. Nación Ahorro"
    )


def test_f32_cuenta_no_cargada_dolares_crear(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("pasé 100 dólares de efectivo dólares a lemon", usuario, db)
    assert res1 is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "No tenés Lemon cargada.\n"
        "1. Crearla\n"
        "2. Era otra de tus cuentas\n"
        "3. Fue un pago a otra persona"
    )

    res2 = simular_mensaje("1", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == (
        "Listo. Creé Lemon y pasaste US$100 de Efectivo Dólares a Lemon."
    )

    b_lemon = db.execute(
        select(Billetera).where(Billetera.nombre == "Lemon")
    ).scalar_one()
    assert b_lemon.moneda == Moneda.USD
    assert b_lemon.saldo_actual == Decimal("100.00")

    b_ef_usd = datos_base["efectivo_usd"]
    db.refresh(b_ef_usd)
    assert b_ef_usd.saldo_actual == Decimal("400.00")


def test_f33_compra_dolares_con_banco_nombrado(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("compré 100 dólares a 1500 con santander", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "Voy a registrar una compra de US$100 a $1.500: "
        "salen $150.000 de Santander y entran US$100 a Efectivo Dólares. ¿Confirmás?"
    )


def test_f34_bloqueo_mezcla_real(db, usuario, datos_base, mensajes_wpp):
    msg = "pasé 20k de galicia a santander y gasté 3000 en el kiosco"
    res = _bloquear_mezcla_en_handlers(
        msg, usuario, db, "5491100000000", "wamid_f34"
    )
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == MSG_NO_MEZCLAR_TRANSFERENCIAS


def test_f35_pregunta_origen_alias_mp(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20 lucas a mp", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "¿Desde qué cuenta pasaste los $20.000 a Mercado Pago?\n\n"
        "1. Galicia\n"
        "2. Efectivo Pesos\n"
        "3. Santander"
    )


def test_f36_falta_monto_y_respuesta_palabras(db, usuario, datos_base, mensajes_wpp):
    res1 = simular_mensaje("pasé plata de galicia a santander", usuario, db)
    assert res1 is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "¿Cuánto pasaste de Galicia a Santander?"

    res2 = simular_mensaje("15 lucas", usuario, db)
    assert res2 is True
    assert len(mensajes_wpp) == 2
    assert mensajes_wpp[1] == "Listo. Pasaste $15.000 de Galicia a Santander."

    b_g = datos_base["galicia"]
    b_s = datos_base["santander"]
    db.refresh(b_g)
    db.refresh(b_s)
    assert b_g.saldo_actual == Decimal("85000.00")
    assert b_s.saldo_actual == Decimal("65000.00")


def test_f37_solo_origen_pregunta_destino(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20k de galicia", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == (
        "¿A qué cuenta pasaste los $20.000 de Galicia?\n\n"
        "1. Efectivo Pesos\n"
        "2. Mercado Pago\n"
        "3. Santander"
    )


def test_f38_transferencia_a_cuenta_inversion(db, usuario, datos_base, mensajes_wpp):
    res = simular_mensaje("pasé 20k de galicia al ahorro", usuario, db)
    assert res is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo. Pasaste $20.000 de Galicia a Ahorro."

    b_g = datos_base["galicia"]
    b_a = datos_base["ahorro"]
    db.refresh(b_g)
    db.refresh(b_a)
    assert b_g.saldo_actual == Decimal("80000.00")
    assert b_a.saldo_actual == Decimal("220000.00")


def test_f39_propiedades_slot_filling_y_llamadas(db, usuario, datos_base, mensajes_wpp):
    casos = [
        "pasé 20k a santander",
        "saqué 10k del cajero",
        "pasé 20k de galicia a macro",
        "pasé 20k de macro a galicia",
        "pasé 100 dólares a galicia",
        "pasé 20 lucas a mp",
        "pasé plata de galicia a santander",
        "pasé 20k de galicia",
    ]
    for msg in casos:
        mensajes_wpp.clear()
        conv_prev = _buscar_slot_filling_activo(usuario.id, db)
        if conv_prev:
            conv_prev.slot_filling_activo = False
            db.commit()

        res = simular_mensaje(msg, usuario, db)
        assert res is True
        assert len(mensajes_wpp) == 1

        conv = _buscar_slot_filling_activo(usuario.id, db)
        assert conv is not None
        assert conv.intent_detectado == "transferir_fondos"
        assert conv.slot_filling_activo is True
        assert conv.slot_filling_estado is not None
        assert conv.slot_filling_estado.get("intent_origen") == "transferir_fondos"
