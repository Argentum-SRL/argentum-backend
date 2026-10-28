# -*- coding: utf-8 -*-
"""
tests/test_wpp_reintegro.py — Tests de detección, búsqueda, registro y anulación de reintegros por WhatsApp.
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

from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.main import app
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.handlers_confirmaciones import manejar_confirmacion
from app.routers.whatsapp.handlers_deshacer import manejar_corregir, manejar_deshacer
from app.routers.whatsapp.que_es_plata_wpp import manejar_respuesta_numerada
from app.routers.whatsapp.reintegro_wpp import (
    detectar_pedido_reintegro,
    manejar_reintegro_wpp,
)
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import reintegro_service, transaccion_service
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
        email="test_reintegros_wpp@argentum.com",
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

    cat_alim = Categoria(id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO)
    cat_hogar = Categoria(id=uuid4(), nombre="Hogar", tipo=TipoCategoria.EGRESO)
    cat_salud = Categoria(id=uuid4(), nombre="Salud", tipo=TipoCategoria.EGRESO)
    cat_otros_ing = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO)
    db.add_all([cat_alim, cat_hogar, cat_salud, cat_otros_ing])
    db.flush()

    sub_super = Subcategoria(id=uuid4(), categoria_id=cat_alim.id, nombre="Supermercado", orden=1)
    sub_muebles = Subcategoria(id=uuid4(), categoria_id=cat_hogar.id, nombre="Muebles y electro", orden=1)
    sub_farmacia = Subcategoria(id=uuid4(), categoria_id=cat_salud.id, nombre="Farmacia", orden=1)
    sub_cobros = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
    db.add_all([sub_super, sub_muebles, sub_farmacia, sub_cobros])

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
        "cat_hogar": cat_hogar,
        "sub_muebles": sub_muebles,
        "cat_salud": cat_salud,
        "sub_farmacia": sub_farmacia,
        "cat_otros_ing": cat_otros_ing,
        "sub_cobros": sub_cobros,
    }


def test_detector():
    assert detectar_pedido_reintegro("me devolvieron 25000 del súper") == (Decimal("25000"), "super")
    assert detectar_pedido_reintegro("Me devolvieron $25.000 en el super.") == (Decimal("25000"), "super")
    assert detectar_pedido_reintegro("ya me llegó el reintegro de 25 mil de kuma") == (Decimal("25000"), "kuma")
    assert detectar_pedido_reintegro("me llegó el reintegro del súper") == (None, "super")
    assert detectar_pedido_reintegro("me reintegraron 5000 por la farmacia") == (Decimal("5000"), "farmacia")
    assert detectar_pedido_reintegro("me devolvieron 25000") is None
    assert detectar_pedido_reintegro("Juan me devolvió 5000") is None
    assert detectar_pedido_reintegro("me devolvieron la plata del súper") is None
    assert detectar_pedido_reintegro("gasté 25000 en el súper") is None
    assert detectar_pedido_reintegro("me devolvieron 0 del súper") is None


def test_tf5_directo(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
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
            origen=OrigenTransaccion.MANUAL,
        ),
    )

    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me devolvieron 25000 del súper", usuario, db, "5491112345678")
    assert ok is True
    assert len(mensajes_wpp) == 1
    assert mensajes_wpp[0] == "Listo, anoté la devolución de $25.000 de «Súper». Si no era esa, decime «borrá eso»."

    # Espejo -25000.00, fecha hoy, Galicia, tipo_espejo "reintegro"
    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_super.id)
    ).scalars().all()
    assert len(espejos) == 1
    esp = espejos[0]
    assert esp.monto == Decimal("-25000.00")
    assert esp.fecha == hoy_argentina()
    assert esp.billetera_id == b_galicia.id
    assert esp.tipo_espejo == "reintegro"

    db.refresh(tx_super)
    assert tx_super.reintegro_esperado == Decimal("25000.00")

    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("925000.00")
    teorico = calcular_saldo_teorico(db, b_galicia.id)
    assert b_galicia.saldo_actual == teorico

    conv = db.execute(
        select(ConversacionWpp)
        .where(ConversacionWpp.usuario_id == usuario.id)
        .order_by(ConversacionWpp.fecha.desc())
    ).scalars().first()
    assert conv.intent_detectado == "registrar_reintegro"
    assert conv.accion_ejecutada == f"reintegro:{tx_super.id}"
    assert (conv.entidades or {}).get("reintegro_esperado_previo") is None


def test_paridad(db: Session, usuario: Usuario, datos_base, client: TestClient, mensajes_wpp):
    b_galicia = datos_base["galicia"]
    # Compra 1: Súper vía WhatsApp
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
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    manejar_reintegro_wpp("me devolvieron 25000 del súper", usuario, db, "5491112345678")
    esp_wpp = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_super.id)
    ).scalars().first()

    # Compra 2: Kuma igual a Súper con PUT /transacciones/{id}/reintegro
    tx_kuma = transaccion_service.crear_transaccion(
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
            descripcion="Kuma",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    resp = client.put(f"/transacciones/{tx_kuma.id}/reintegro", json={"monto": 25000, "recibido": True})
    assert resp.status_code == 200

    esp_web = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_kuma.id)
    ).scalars().first()

    assert esp_wpp.monto == esp_web.monto == Decimal("-25000.00")
    assert esp_wpp.fecha == esp_web.fecha
    assert esp_wpp.billetera_id == esp_web.billetera_id
    assert esp_wpp.metodo_pago == esp_web.metodo_pago
    assert esp_wpp.categoria_id == esp_web.categoria_id
    assert esp_wpp.subcategoria_id == esp_web.subcategoria_id
    assert esp_wpp.tipo_espejo == esp_web.tipo_espejo == "reintegro"


def test_credito(db: Session, usuario: Usuario, datos_base, client: TestClient, mensajes_wpp):
    tarjeta = datos_base["tarjeta"]
    ayer = hoy_argentina() - timedelta(days=1)
    tx_tele = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.CREDITO,
            tarjeta_id=tarjeta.id,
            billetera_id=datos_base["galicia"].id,
            categoria_id=datos_base["cat_hogar"].id,
            subcategoria_id=datos_base["sub_muebles"].id,
            fecha=ayer,
            descripcion="Tele",
            info_cuotas=InfoCuotas(cantidad_cuotas=1, monto_total=Decimal("30000.00")),
            origen=OrigenTransaccion.MANUAL,
        ),
    )

    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me devolvieron 10000 de la tele", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "Listo, anoté la devolución de $10.000 de «Tele» (ayer). Si no era esa, decime «borrá eso»."

    esp_padre = db.execute(
        select(Transaccion).where(
            Transaccion.reintegro_de_id == tx_tele.id,
            Transaccion.es_cuota_hija.is_(False),
        )
    ).scalars().first()
    esp_hija = db.execute(
        select(Transaccion).where(
            Transaccion.reintegro_de_id == tx_tele.id,
            Transaccion.es_cuota_hija.is_(True),
        )
    ).scalars().first()
    assert esp_padre is not None
    assert esp_hija is not None

    # Otra compra idéntica para comparar con la ruta PUT
    tx_tele_web = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.CREDITO,
            tarjeta_id=tarjeta.id,
            billetera_id=datos_base["galicia"].id,
            categoria_id=datos_base["cat_hogar"].id,
            subcategoria_id=datos_base["sub_muebles"].id,
            fecha=ayer,
            descripcion="Tele 2",
            info_cuotas=InfoCuotas(cantidad_cuotas=1, monto_total=Decimal("30000.00")),
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    client.put(f"/transacciones/{tx_tele_web.id}/reintegro", json={"monto": 10000, "recibido": True})
    esp_hija_web = db.execute(
        select(Transaccion).where(
            Transaccion.reintegro_de_id == tx_tele_web.id,
            Transaccion.es_cuota_hija.is_(True),
        )
    ).scalars().first()
    assert esp_hija.fecha == esp_hija_web.fecha


def test_subcategoria(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("50000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.DEBITO,
            categoria_id=datos_base["cat_alim"].id,
            subcategoria_id=datos_base["sub_super"].id,
            billetera_id=datos_base["galicia"].id,
            fecha=hoy_argentina(),
            descripcion="Coto",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me devolvieron 5000 del super", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "Listo, anoté la devolución de $5.000 de «Coto». Si no era esa, decime «borrá eso»."


def test_deshacer_sin_previo(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
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
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    manejar_reintegro_wpp("me devolvieron 25000 del súper", usuario, db, "5491112345678")

    mensajes_wpp.clear()
    ok_deshacer = manejar_deshacer("borrá eso", usuario, db, "5491112345678")
    assert ok_deshacer is True
    assert mensajes_wpp[-1] == "¿Querés eliminar la devolución de $25.000 de «Súper» en Galicia? ¿Confirmás?"

    mensajes_wpp.clear()
    ok_conf = manejar_confirmacion("sí", usuario, db, "5491112345678")
    assert ok_conf is True
    assert mensajes_wpp[-1] == "Listo, devolución eliminada."

    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_super.id)
    ).scalars().all()
    assert len(espejos) == 0

    db.refresh(tx_super)
    assert tx_super.reintegro_esperado is None

    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("900000.00")
    assert b_galicia.saldo_actual == calcular_saldo_teorico(db, b_galicia.id)


def test_pendiente(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    b_galicia = datos_base["galicia"]
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
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, tx_super.id, monto=Decimal("25000.00"), recibido=False
    )
    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("900000.00")

    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me llegó el reintegro del súper", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "Listo, anoté la devolución de $25.000 de «Súper». Si no era esa, decime «borrá eso»."
    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("925000.00")

    # Deshacer y confirmar
    mensajes_wpp.clear()
    manejar_deshacer("borrá eso", usuario, db, "5491112345678")
    manejar_confirmacion("sí", usuario, db, "5491112345678")
    assert mensajes_wpp[-1] == "Listo, devolución eliminada."

    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_super.id)
    ).scalars().all()
    assert len(espejos) == 0

    db.refresh(tx_super)
    assert tx_super.reintegro_esperado == Decimal("25000.00")
    db.refresh(b_galicia)
    assert b_galicia.saldo_actual == Decimal("900000.00")


def test_sin_monto_y_sin_esperado(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
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
            billetera_id=datos_base["galicia"].id,
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me llegó el reintegro del súper", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "¿Cuánto te devolvieron? Decímelo con el monto, por ejemplo «me devolvieron 25000 del súper»."
    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_super.id)
    ).scalars().all()
    assert len(espejos) == 0


def test_ya_recibido(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
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
            billetera_id=datos_base["galicia"].id,
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, tx_super.id, monto=Decimal("25000.00"), recibido=True
    )
    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me devolvieron 5000 del súper", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "Esa compra ya tiene su reintegro cargado. Para cambiarlo, entrá a la web de Argentum."
    esp = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_super.id)
    ).scalars().first()
    assert esp.monto == Decimal("-25000.00")


def test_mayor(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
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
            billetera_id=datos_base["galicia"].id,
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me devolvieron 150000 del súper", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "El reintegro no puede ser mayor que la compra."
    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_super.id)
    ).scalars().all()
    assert len(espejos) == 0


def test_varias(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    ayer = hoy_argentina() - timedelta(days=1)
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.DEBITO,
            categoria_id=datos_base["cat_alim"].id,
            subcategoria_id=datos_base["sub_super"].id,
            billetera_id=datos_base["galicia"].id,
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    tx_ayer = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("80000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.DEBITO,
            categoria_id=datos_base["cat_alim"].id,
            subcategoria_id=datos_base["sub_super"].id,
            billetera_id=datos_base["galicia"].id,
            fecha=ayer,
            descripcion="Súper",
            origen=OrigenTransaccion.MANUAL,
        ),
    )

    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me devolvieron 25000 del súper", usuario, db, "5491112345678")
    assert ok is True
    assert mensajes_wpp[0] == "¿De cuál compra?\n1) Súper $100.000\n2) Súper $80.000 (ayer)\nRespondé con el número."

    # "3" en vez de "2" -> False
    assert manejar_respuesta_numerada("3", usuario, db, "5491112345678") is False

    # "2" -> True y registra reintegro en la de 80.000
    mensajes_wpp.clear()
    ok_resp = manejar_respuesta_numerada("2", usuario, db, "5491112345678")
    assert ok_resp is True
    assert mensajes_wpp[0] == "Listo, anoté la devolución de $25.000 de «Súper» (ayer). Si no era esa, decime «borrá eso»."

    esp_ayer = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == tx_ayer.id)
    ).scalars().first()
    assert esp_ayer is not None
    assert esp_ayer.monto == Decimal("-25000.00")


def test_sin_coincidencia(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    mensajes_wpp.clear()
    ok = manejar_reintegro_wpp("me devolvieron 25000 de la farmacia", usuario, db, "5491112345678")
    assert ok is False
    assert len(mensajes_wpp) == 0


def test_corregir(db: Session, usuario: Usuario, datos_base, mensajes_wpp):
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            metodo_pago=MetodoPago.DEBITO,
            categoria_id=datos_base["cat_alim"].id,
            subcategoria_id=datos_base["sub_super"].id,
            billetera_id=datos_base["galicia"].id,
            fecha=hoy_argentina(),
            descripcion="Súper",
            origen=OrigenTransaccion.MANUAL,
        ),
    )
    manejar_reintegro_wpp("me devolvieron 25000 del súper", usuario, db, "5491112345678")

    mensajes_wpp.clear()
    ok_corr = manejar_corregir("no, eran 20000", usuario, db, "5491112345678")
    assert ok_corr is True
    assert mensajes_wpp[-1] == "Ese movimiento es una devolución de un gasto. Para cambiarlo, entrá a la web de Argentum."
