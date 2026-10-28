# -*- coding: utf-8 -*-
"""
tests/test_wpp_dolares_desde_pesos.py — Pruebas de gastos en dólares desde billeteras en pesos por WhatsApp.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.dolares_desde_pesos import (
    billetera_pesos_para_usd,
    extraer_monto_pesos,
    manejar_monto_pesos_usd,
    texto_propuesta_usd_pesos,
)
from app.routers.whatsapp.registro import (
    _confirmar_propuesta_transaccion,
    _es_registro_directo,
)
from app.services import dolar_service
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
        email="test_usd_pesos@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(name="datos_base")
def datos_base_fixture(db: Session, usuario: Usuario):
    # Billetera "Galicia" en pesos, principal, con saldo 1.000.000 y fecha del saldo 01/09/2026
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
    # "Mercado Pago" en pesos con saldo 500.000
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

    # Categorías y subcategorías
    cat_ocio = Categoria(id=uuid4(), nombre="Ocio", tipo=TipoCategoria.EGRESO)
    cat_alim = Categoria(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO)
    cat_hogar = Categoria(id=uuid4(), nombre="Hogar", tipo=TipoCategoria.EGRESO)
    cat_otros_ing = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO)
    db.add_all([cat_ocio, cat_alim, cat_hogar, cat_otros_ing])
    db.flush()

    sub_hobbies = Subcategoria(id=uuid4(), categoria_id=cat_ocio.id, nombre="Hobbies y juegos", orden=1)
    sub_salidas = Subcategoria(id=uuid4(), categoria_id=cat_ocio.id, nombre="Salidas", orden=2)
    sub_super = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Supermercado", orden=1)
    sub_muebles = Subcategoria(id=uuid4(), categoria_id=cat_hogar.id, nombre="Muebles y electro", orden=1)
    sub_cobros = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
    db.add_all([sub_hobbies, sub_salidas, sub_super, sub_muebles, sub_cobros])

    # Cotización "oficial" con fecha hoy_argentina(), compra 950, venta 1000 y promedio 975
    cotiz = CotizacionDolar(
        id=uuid4(),
        fecha=hoy_argentina(),
        tipo="oficial",
        compra=Decimal("950.0000"),
        venta=Decimal("1000.0000"),
        promedio=Decimal("975.0000"),
    )
    db.add(cotiz)

    # Tarjeta "Visa" de Galicia con cierre 22 y vencimiento 4
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
        "sub_hobbies": sub_hobbies,
    }


def test_cuenta_sugerir_pesos(db: Session, client: TestClient, datos_base):
    # Cuenta: sugerir_pesos_para_usd(db, Decimal("20")) → cotizacion 1000, percepcion 30, monto_pesos Decimal("26000.00"), cotizacion_tarjeta Decimal("1300.00")
    res = dolar_service.sugerir_pesos_para_usd(db, Decimal("20"))
    assert res["cotizacion"] == Decimal("1000.0000")
    assert res["percepcion"] == 30
    assert res["monto_pesos"] == Decimal("26000.00")
    assert res["cotizacion_tarjeta"] == Decimal("1300.00")

    # con fecha date(2020, 1, 1) → cotizacion, monto_pesos y cotizacion_tarjeta None y percepcion 30
    res_viejo = dolar_service.sugerir_pesos_para_usd(db, Decimal("20"), fecha=date(2020, 1, 1))
    assert res_viejo["cotizacion"] is None
    assert res_viejo["monto_pesos"] is None
    assert res_viejo["cotizacion_tarjeta"] is None
    assert res_viejo["percepcion"] == 30

    # GET /transacciones/sugerencia-pesos?monto_usd=20
    resp = client.get("/transacciones/sugerencia-pesos?monto_usd=20")
    assert resp.status_code == 200
    esperado_api = {
        "cotizacion": 1000.0,
        "percepcion": 30,
        "monto_pesos": 26000.0,
        "cotizacion_tarjeta": 1300.0,
    }
    assert resp.json() == esperado_api
    assert resp.json() == {
        "cotizacion": float(res["cotizacion"]),
        "percepcion": res["percepcion"],
        "monto_pesos": float(res["monto_pesos"]),
        "cotizacion_tarjeta": float(res["cotizacion_tarjeta"]),
    }


def test_extraer_monto_pesos():
    assert extraer_monto_pesos("25800") == Decimal("25800")
    assert extraer_monto_pesos("$25.800") == Decimal("25800")
    assert extraer_monto_pesos("fueron 25.800") == Decimal("25800")
    assert extraer_monto_pesos("no, fueron 25800") == Decimal("25800")
    assert extraer_monto_pesos("me cobraron 25800 pesos") == Decimal("25800")
    assert extraer_monto_pesos("26 mil") == Decimal("26000")
    assert extraer_monto_pesos("25.800,50") == Decimal("25800.50")
    assert extraer_monto_pesos("fueron 25 dólares") is None
    assert extraer_monto_pesos("25800 en el súper") is None
    assert extraer_monto_pesos("hola") is None
    assert extraer_monto_pesos("0") is None


def test_billetera_pesos_para_usd(db: Session, usuario: Usuario, datos_base):
    b_galicia = datos_base["galicia"]
    b_mp = datos_base["mp"]

    b_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Dólares",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("100.00"),
        saldo_actual=Decimal("100.00"),
        fecha_saldo=date(2026, 9, 1),
        es_principal=False,
    )
    db.add(b_usd)
    db.commit()

    tres = [b_galicia, b_mp, b_usd]

    # {"billetera_origen": "Galicia"} con las tres → Galicia
    assert billetera_pesos_para_usd({"billetera_origen": "Galicia"}, tres, "billetera_origen", "billetera_destino") == b_galicia

    # {"billetera_origen": "Dólares"} → None
    assert billetera_pesos_para_usd({"billetera_origen": "Dólares"}, tres, "billetera_origen", "billetera_destino") is None

    # {} con las tres → None
    assert billetera_pesos_para_usd({}, tres, "billetera_origen", "billetera_destino") is None

    # {} con Galicia (principal) y Mercado Pago → Galicia
    dos_gal_ppal = [b_galicia, b_mp]
    assert billetera_pesos_para_usd({}, dos_gal_ppal, "billetera_origen", "billetera_destino") == b_galicia

    # {} con solo Mercado Pago (no principal) → Mercado Pago
    b_mp.es_principal = False
    assert billetera_pesos_para_usd({}, [b_mp], "billetera_origen", "billetera_destino") == b_mp

    # {} con Galicia y Mercado Pago, ninguna principal → None
    b_galicia.es_principal = False
    b_mp.es_principal = False
    assert billetera_pesos_para_usd({}, [b_galicia, b_mp], "billetera_origen", "billetera_destino") is None
    b_galicia.es_principal = True


def test_textos_propuesta_usd_pesos():
    ayer_iso = (hoy_argentina() - timedelta(days=1)).isoformat()

    # con monto y monto_editado falso
    e1 = {
        "monto_original": 20,
        "monto": 26000.0,
        "cotizacion_sugerida": 1000.0,
        "percepcion": 30,
        "categoria": "Hobbies y juegos",
        "fecha": None,
        "monto_editado": False,
    }
    assert texto_propuesta_usd_pesos(e1, "Galicia") == (
        "Voy a anotar US$20 en Hobbies y juegos desde Galicia: son $26.000 (oficial $1.000 + 30%).\n"
        "¿Confirmás? Si te cobraron otro monto en pesos, decime cuánto."
    )

    # con monto 25800.0 y monto_editado True
    e2 = {
        "monto_original": 20,
        "monto": 25800.0,
        "cotizacion_sugerida": 1000.0,
        "percepcion": 30,
        "categoria": "Hobbies y juegos",
        "fecha": None,
        "monto_editado": True,
    }
    assert texto_propuesta_usd_pesos(e2, "Galicia") == (
        "Voy a anotar US$20 en Hobbies y juegos desde Galicia: son $25.800.\n¿Confirmás?"
    )

    # con monto None
    e3 = {
        "monto_original": 20,
        "monto": None,
        "cotizacion_sugerida": None,
        "percepcion": 30,
        "categoria": "Hobbies y juegos",
        "fecha": None,
        "monto_editado": False,
    }
    assert texto_propuesta_usd_pesos(e3, "Galicia") == (
        "Voy a anotar US$20 en Hobbies y juegos desde Galicia.\n¿Cuántos pesos te cobraron?"
    )

    # con fecha = ayer en ISO
    e4 = {
        "monto_original": 20,
        "monto": 26000.0,
        "cotizacion_sugerida": 1000.0,
        "percepcion": 30,
        "categoria": "Hobbies y juegos",
        "fecha": ayer_iso,
        "monto_editado": False,
    }
    assert texto_propuesta_usd_pesos(e4, "Galicia") == (
        "Voy a anotar US$20 en Hobbies y juegos desde Galicia (ayer): son $26.000 (oficial $1.000 + 30%).\n"
        "¿Confirmás? Si te cobraron otro monto en pesos, decime cuánto."
    )


def test_es_registro_directo():
    c_wpp = ConversacionWpp(
        id=uuid4(),
        usuario_id=uuid4(),
        mensaje_usuario="gaste 20 dolares",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        confianza=Decimal("0.950"),
        slot_filling_activo=False,
        entidades={
            "monto": 26000.0,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": "Hobbies y juegos",
            "billetera_origen": "Galicia",
            "tipo_flujo": "propuesta_usd_pesos",
        },
    )
    assert _es_registro_directo(c_wpp) is False

    c_wpp_sin_flujo = ConversacionWpp(
        id=uuid4(),
        usuario_id=uuid4(),
        mensaje_usuario="gaste 20 dolares",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        confianza=Decimal("0.950"),
        slot_filling_activo=False,
        entidades={
            "monto": 26000.0,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": "Hobbies y juegos",
            "billetera_origen": "Galicia",
        },
    )
    assert _es_registro_directo(c_wpp_sin_flujo) is True


def test_confirmacion_usd_pesos(db: Session, usuario: Usuario, datos_base):
    b_galicia = datos_base["galicia"]

    # ConversacionWpp guardada
    conv = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 20 dólares en steam",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
        entidades={
            "monto": 26000.0,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": "Hobbies y juegos",
            "descripcion": "Steam",
            "billetera_origen": "Galicia",
            "billetera": "Galicia",
            "moneda_original": "USD",
            "monto_original": 20.0,
            "cotizacion_sugerida": 1000.0,
            "percepcion": 30,
            "tipo_flujo": "propuesta_usd_pesos",
            "monto_editado": False,
        },
    )
    db.add(conv)
    db.commit()

    tx, msg, ya_conf = _confirmar_propuesta_transaccion(usuario, db)
    assert tx is not None
    assert tx.monto == Decimal("26000.00")
    assert tx.moneda == Moneda.ARS
    assert tx.monto_original == Decimal("20.00")
    assert tx.moneda_original == Moneda.USD
    assert tx.cotizacion_aplicada == Decimal("1300.0000")
    assert tx.tipo_dolar_usado == "tarjeta"
    assert tx.descripcion == "Steam"
    assert tx.subcategoria.nombre == "Hobbies y juegos"
    assert tx.billetera.nombre == "Galicia"

    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("974000.00")
    assert calcular_saldo_teorico(db, b_galicia.id) == b_galicia.saldo_actual

    primera_linea = msg.splitlines()[0]
    assert primera_linea == "Listo. US$20 ($26.000) en Hobbies y juegos desde Galicia — registrado."
    assert conv.accion_ejecutada == str(tx.id)

    # Con "monto": None → (None, "Decime cuántos pesos te cobraron.", False)
    conv_none = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 20 dólares",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
        entidades={
            "monto": None,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": "Hobbies y juegos",
            "descripcion": "Steam",
            "billetera_origen": "Galicia",
            "billetera": "Galicia",
            "moneda_original": "USD",
            "monto_original": 20.0,
            "cotizacion_sugerida": None,
            "percepcion": 30,
            "tipo_flujo": "propuesta_usd_pesos",
            "monto_editado": False,
        },
    )
    db.add(conv_none)
    db.commit()

    cant_tx_antes = db.execute(select(Transaccion)).scalars().all()
    tx_res, msg_res, ya_c = _confirmar_propuesta_transaccion(usuario, db)
    assert tx_res is None
    assert msg_res == "Decime cuántos pesos te cobraron."
    assert ya_c is False
    assert conv_none.accion_ejecutada is None
    cant_tx_desp = db.execute(select(Transaccion)).scalars().all()
    assert len(cant_tx_desp) == len(cant_tx_antes)


def test_monto_nuevo_y_edicion(db: Session, usuario: Usuario, datos_base, monkeypatch):
    mensajes_enviados = []

    def mock_enviar_whatsapp(to, text, **kwargs):
        mensajes_enviados.append(text)
        return True

    monkeypatch.setattr("app.services.whatsapp_service.enviar_whatsapp", mock_enviar_whatsapp)
    b_galicia = datos_base["galicia"]

    # Propuesta guardada
    prop = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 20 dólares en steam",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
        entidades={
            "monto": 26000.0,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": "Hobbies y juegos",
            "descripcion": "Steam",
            "billetera_origen": "Galicia",
            "billetera": "Galicia",
            "moneda_original": "USD",
            "monto_original": 20.0,
            "cotizacion_sugerida": 1000.0,
            "percepcion": 30,
            "tipo_flujo": "propuesta_usd_pesos",
            "monto_editado": False,
        },
    )
    db.add(prop)
    db.commit()

    ok = manejar_monto_pesos_usd("fueron 25.800", usuario, db, "5491112345678")
    assert ok is True
    assert len(mensajes_enviados) == 1
    assert mensajes_enviados[-1] == (
        "Voy a anotar US$20 en Hobbies y juegos desde Galicia: son $25.800.\n¿Confirmás?"
    )
    db.refresh(prop)
    assert prop.entidades["monto"] == 25800.0
    assert prop.entidades["monto_editado"] is True

    # Después _confirmar_propuesta_transaccion
    tx, msg, _ = _confirmar_propuesta_transaccion(usuario, db)
    assert tx is not None
    assert tx.monto == Decimal("25800.00")
    assert tx.cotizacion_aplicada == Decimal("1290.0000")
    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("974200.00")

    # manejar_monto_pesos_usd con "fueron 25 dólares" → False
    assert manejar_monto_pesos_usd("fueron 25 dólares", usuario, db, "5491112345678") is False

    # Con una propuesta guardada sin tipo_flujo → False
    prop_normal = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        mensaje_usuario="gasté 5000",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="bot",
        intent_detectado="registrar_transaccion",
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
        entidades={"monto": 5000.0, "moneda": "ARS"},
    )
    db.add(prop_normal)
    db.commit()
    assert manejar_monto_pesos_usd("25800", usuario, db, "5491112345678") is False

