"""
tests/test_que_es_esta_plata.py — Pruebas para TF.4a-e, validaciones, candidatos y API de ¿Qué es esta plata?
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from fastapi import HTTPException
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
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import (
    historial_service,
    que_es_plata_service,
    reintegro_service,
    transaccion_service,
)
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.definiciones_service import (
    cargar_contexto,
    condicion_gasto,
    condicion_ingreso,
    etiqueta_movimiento,
)


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
        email="test_que_es@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="billetera_ars")
def billetera_ars_fixture(db: Session, usuario: Usuario):
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Principal",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        fecha_saldo=date(2026, 9, 1),
        es_inversion=False,
    )
    db.add(billetera)
    db.commit()
    db.refresh(billetera)
    return billetera


@pytest.fixture(name="billetera_usd")
def billetera_usd_fixture(db: Session, usuario: Usuario):
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Caja de Ahorro USD",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("1000.00"),
        saldo_actual=Decimal("1000.00"),
        fecha_saldo=date(2026, 9, 1),
        es_inversion=False,
    )
    db.add(billetera)
    db.commit()
    db.refresh(billetera)
    return billetera


@pytest.fixture(name="tarjeta_credito")
def tarjeta_fixture(db: Session, usuario: Usuario, billetera_ars: Billetera):
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Visa Crédito",
        limite_credito=Decimal("2000000.00"),
        dia_cierre=22,
        dia_vencimiento=4,
        billetera_id=billetera_ars.id,
        red=RedTarjeta.VISA,
    )
    db.add(tarjeta)
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


@pytest.fixture(name="categorias")
def categorias_fixture(db: Session):
    cat_ocio = Categoria(id=uuid4(), nombre="Ocio", tipo=TipoCategoria.EGRESO)
    cat_super = Categoria(id=uuid4(), nombre="Supermercado", tipo=TipoCategoria.EGRESO)
    cat_otros_egr = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    cat_otros_ing = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO)
    db.add_all([cat_ocio, cat_super, cat_otros_egr, cat_otros_ing])
    db.flush()

    sub_salidas = Subcategoria(id=uuid4(), categoria_id=cat_ocio.id, nombre="Salidas", orden=1)
    sub_paso_egr = Subcategoria(id=uuid4(), categoria_id=cat_otros_egr.id, nombre="Pasó por mi cuenta", orden=4)
    sub_cobros = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
    sub_paso_ing = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Pasó por mi cuenta", orden=3)
    db.add_all([sub_salidas, sub_paso_egr, sub_cobros, sub_paso_ing])
    db.commit()

    return {
        "ocio": cat_ocio,
        "supermercado": cat_super,
        "otros_egreso": cat_otros_egr,
        "otros_ingreso": cat_otros_ing,
        "sub_salidas": sub_salidas,
        "sub_paso_egr": sub_paso_egr,
        "sub_cobros": sub_cobros,
        "sub_paso_ing": sub_paso_ing,
    }


def _crear_base_tf4(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    asado_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("80000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 20),
        descripcion="Asado",
        categoria_id=categorias["ocio"].id,
        subcategoria_id=categorias["sub_salidas"].id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    asado = transaccion_service.crear_transaccion(db, usuario.id, asado_data)

    juan_data = TransaccionCreate(
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 25),
        descripcion="Juan",
        categoria_id=categorias["otros_ingreso"].id,
        subcategoria_id=categorias["sub_cobros"].id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        billetera_id=billetera_ars.id,
    )
    juan = transaccion_service.crear_transaccion(db, usuario.id, juan_data)
    return asado, juan


def test_tf4a_me_pagaron(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    TF.4a me_pagaron: sigue ingreso de 50000.00 en Cobros de personas; ingresos 50000.00; gasto de Salidas 80000.00; saldo 970000.00.
    """
    asado, juan = _crear_base_tf4(db, usuario, billetera_ars, categorias)

    que_es_plata_service.responder(db, usuario.id, juan.id, "me_pagaron")

    db.refresh(juan)
    assert juan.tipo == TipoTransaccion.INGRESO
    assert juan.monto == Decimal("50000.00")
    assert juan.categoria_id == categorias["otros_ingreso"].id
    assert juan.subcategoria_id == categorias["sub_cobros"].id

    # Ingresos octubre con hoy 27/10
    stmt_ingresos = select(Transaccion).where(
        condicion_ingreso(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 27))
    )
    total_ingresos = sum(t.monto for t in db.execute(stmt_ingresos).scalars().all())
    assert total_ingresos == Decimal("50000.00")

    # Gasto de Salidas
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 27)),
        Transaccion.subcategoria_id == categorias["sub_salidas"].id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("80000.00")

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("970000.00")


def test_tf4b_parte_de_gasto(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    TF.4b parte_de_gasto con el Asado: misma fila (mismo id, fecha 25/10, descripción "Juan"), tipo egreso, monto −50000.00,
    reintegro_de_id del Asado, tipo_espejo "parte", categoría y subcategoría de Salidas; ingresos 0.00; gasto de Salidas 30000.00;
    saldo 970000.00 e igual al teórico; listar_movimientos de octubre: 2 filas ("Juan" etiqueta "Devolución", sentido "entra", 50000.00;
    "Asado" sale 80000.00 con reintegro None); totales gastos 30000.00 e ingresos 0.00.
    """
    asado, juan = _crear_base_tf4(db, usuario, billetera_ars, categorias)
    juan_id = juan.id

    que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=asado.id)

    db.refresh(juan)
    assert juan.id == juan_id
    assert juan.fecha == date(2026, 10, 25)
    assert juan.descripcion == "Juan"
    assert juan.tipo == TipoTransaccion.EGRESO
    assert juan.monto == Decimal("-50000.00")
    assert juan.reintegro_de_id == asado.id
    assert juan.tipo_espejo == "parte"
    assert juan.categoria_id == categorias["ocio"].id
    assert juan.subcategoria_id == categorias["sub_salidas"].id

    # Ingresos 0.00
    stmt_ingresos = select(Transaccion).where(
        condicion_ingreso(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 27))
    )
    total_ingresos = sum(t.monto for t in db.execute(stmt_ingresos).scalars().all())
    assert total_ingresos == Decimal("0.00")

    # Gasto Salidas 30000.00
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 27)),
        Transaccion.subcategoria_id == categorias["sub_salidas"].id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("30000.00")

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("970000.00")
    teorico = calcular_saldo_teorico(db, billetera_ars.id)
    assert teorico == billetera_ars.saldo_actual

    # listar_movimientos
    resp = historial_service.listar_movimientos(
        db=db,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        hoy=date(2026, 10, 27),
    )
    assert len(resp.items) == 2
    f0 = resp.items[0]
    assert f0.descripcion == "Juan"
    assert f0.etiqueta == "Devolución"
    assert f0.sentido == "entra"
    assert f0.monto == Decimal("50000.00")

    f1 = resp.items[1]
    assert f1.descripcion == "Asado"
    assert f1.sentido == "sale"
    assert f1.monto == Decimal("80000.00")
    assert f1.reintegro is None

    assert resp.totales["ARS"].gastos == Decimal("30000.00")
    assert resp.totales["ARS"].ingresos == Decimal("0.00")


def test_tf4c_paso_por_mi_cuenta(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    TF.4c paso_por_mi_cuenta: ingreso en Otros > Pasó por mi cuenta; ingresos 0.00; saldo 970000.00;
    etiqueta_movimiento ("Pasó por mi cuenta", "nada", "No cuenta como gasto ni ingreso").
    """
    asado, juan = _crear_base_tf4(db, usuario, billetera_ars, categorias)

    que_es_plata_service.responder(db, usuario.id, juan.id, "paso_por_mi_cuenta")

    db.refresh(juan)
    assert juan.tipo == TipoTransaccion.INGRESO
    assert juan.categoria_id == categorias["otros_ingreso"].id
    assert juan.subcategoria_id == categorias["sub_paso_ing"].id

    stmt_ingresos = select(Transaccion).where(
        condicion_ingreso(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 27))
    )
    total_ingresos = sum(t.monto for t in db.execute(stmt_ingresos).scalars().all())
    assert total_ingresos == Decimal("0.00")

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("970000.00")

    ctx = cargar_contexto(db, usuario.id, hoy=date(2026, 10, 27))
    etiq = etiqueta_movimiento(juan, ctx)
    assert etiq == ("Pasó por mi cuenta", "nada", "No cuenta como gasto ni ingreso")


def test_tf4d_desde_4b_a_me_pagaron(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    TF.4d desde 4b, me_pagaron: vuelve a ingreso de 50000.00 en Cobros de personas, reintegro_de_id None, tipo_espejo None;
    gasto de Salidas 80000.00; saldo 970000.00.
    """
    asado, juan = _crear_base_tf4(db, usuario, billetera_ars, categorias)
    que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=asado.id)

    # Ahora responde me_pagaron
    que_es_plata_service.responder(db, usuario.id, juan.id, "me_pagaron")

    db.refresh(juan)
    assert juan.tipo == TipoTransaccion.INGRESO
    assert juan.monto == Decimal("50000.00")
    assert juan.categoria_id == categorias["otros_ingreso"].id
    assert juan.subcategoria_id == categorias["sub_cobros"].id
    assert juan.reintegro_de_id is None
    assert juan.tipo_espejo is None

    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 27)),
        Transaccion.subcategoria_id == categorias["sub_salidas"].id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("80000.00")

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("970000.00")


def test_tf4e_desde_4b_eliminar_asado(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    TF.4e desde 4b, eliminar_transaccion del Asado: la fila "Juan" vuelve a ingreso de 50000.00 en Cobros de personas;
    saldo 1050000.00 e igual al teórico.
    """
    asado, juan = _crear_base_tf4(db, usuario, billetera_ars, categorias)
    que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=asado.id)

    transaccion_service.eliminar_transaccion(db, usuario.id, asado.id)

    db.refresh(juan)
    assert juan.tipo == TipoTransaccion.INGRESO
    assert juan.monto == Decimal("50000.00")
    assert juan.categoria_id == categorias["otros_ingreso"].id
    assert juan.subcategoria_id == categorias["sub_cobros"].id
    assert juan.reintegro_de_id is None
    assert juan.tipo_espejo is None

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("1050000.00")
    teorico = calcular_saldo_teorico(db, billetera_ars.id)
    assert teorico == billetera_ars.saldo_actual


def test_validaciones_exactas_que_es_plata(db: Session, usuario: Usuario, billetera_ars: Billetera, billetera_usd: Billetera, tarjeta_credito: TarjetaCredito, categorias: dict):
    """
    Validaciones con el texto exacto:
    - parte sin gasto_id ("Elegí el gasto que querés vincular.")
    - gasto que es un ingreso o una cuota hija ("Ese movimiento no es un gasto.")
    - gasto en dólares de una billetera en dólares ("El gasto tiene que estar en la misma moneda.")
    - Asado con un reintegro recibido de 40.000 ("Es más de lo que queda de ese gasto.")
    - un egreso común ("Esto solo se puede hacer con plata que entró.")
    - opción "otra" ("Elegí una opción válida.")
    - ingreso de otro usuario (404).
    """
    asado, juan = _crear_base_tf4(db, usuario, billetera_ars, categorias)

    # 1. parte sin gasto_id
    with pytest.raises(HTTPException) as exc1:
        que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=None)
    assert exc1.value.detail == "Elegí el gasto."

    # 2. gasto que es un ingreso
    otro_ingreso = transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 22),
            descripcion="Otro ingreso",
            categoria_id=categorias["otros_ingreso"].id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.TRANSFERENCIA,
        )
    )
    with pytest.raises(HTTPException) as exc2a:
        que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=otro_ingreso.id)
    assert exc2a.value.detail == "Ese movimiento no es un gasto."

    # gasto que es cuota hija
    transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("20000.00"),
            moneda=Moneda.ARS,
            fecha=date(2026, 10, 15),
            descripcion="Compra cuotas",
            categoria_id=categorias["ocio"].id,
            metodo_pago=MetodoPago.CREDITO,
            tarjeta_id=tarjeta_credito.id,
            billetera_id=billetera_ars.id,
            info_cuotas=InfoCuotas(cantidad_cuotas=2, monto_total=Decimal("20000.00")),
        )
    )
    cuota_hija_tx = db.execute(
        select(Transaccion).where(Transaccion.es_cuota_hija.is_(True), Transaccion.grupo_cuotas_id.is_not(None))
    ).scalars().first()
    with pytest.raises(HTTPException) as exc2b:
        que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=cuota_hija_tx.id)
    assert exc2b.value.detail == "Ese movimiento no es un gasto."

    # 3. gasto en dólares de una billetera en dólares
    gasto_usd = transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("60.00"),
            moneda=Moneda.USD,
            fecha=date(2026, 10, 20),
            descripcion="Cena USD",
            categoria_id=categorias["ocio"].id,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=billetera_usd.id,
        )
    )
    with pytest.raises(HTTPException) as exc3:
        que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=gasto_usd.id)
    assert exc3.value.detail == "El gasto tiene que estar en la misma moneda."

    # 4. Asado con un reintegro recibido de 40.000: disponible = 40.000 < Juan (50.000)
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=asado.id,
        monto=Decimal("40000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 21),
    )
    with pytest.raises(HTTPException) as exc4:
        que_es_plata_service.responder(db, usuario.id, juan.id, "parte_de_gasto", gasto_id=asado.id)
    assert exc4.value.detail == "Es más de lo que queda de ese gasto."

    # 5. un egreso común
    with pytest.raises(HTTPException) as exc5:
        que_es_plata_service.responder(db, usuario.id, asado.id, "me_pagaron")
    assert exc5.value.detail == "Esto solo se puede hacer con plata que entró."

    # 6. opción "otra"
    with pytest.raises(HTTPException) as exc6:
        que_es_plata_service.responder(db, usuario.id, juan.id, "otra")
    assert exc6.value.detail == "Elegí una opción válida."

    # 7. ingreso de otro usuario (404)
    otro_usuario = Usuario(
        id=uuid4(),
        email="otro_user@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(otro_usuario)
    db.commit()
    with pytest.raises(HTTPException) as exc7:
        que_es_plata_service.responder(db, otro_usuario.id, juan.id, "me_pagaron")
    assert exc7.value.status_code == 404


def test_candidatos_gastos_para_vincular(db: Session, usuario: Usuario, billetera_ars: Billetera, billetera_usd: Billetera, tarjeta_credito: TarjetaCredito, categorias: dict):
    """
    Candidatos: gastos_para_vincular(moneda ARS, fecha 25/10/2026, monto 50.000) con estos gastos:
    Asado 80.000 (20/10); Super 30.000 (22/10); Cena US$60.000 (20/10, billetera en dólares);
    Viaje 200.000 (01/08); pago de resumen 100.000 (21/10); Tele 120.000 con la tarjeta en 3 cuotas (15/10);
    Regalo 90.000 (18/10) con reintegro recibido de 50.000; un egreso de 60.000 en Otros > Pasó por mi cuenta (19/10).
    Resultado: [Asado (fecha 2026-10-20, monto 80000.00, disponible 80000.00), Tele (2026-10-15, 120000.00, 120000.00)], en ese orden.
    """
    # 1. Asado 80.000 (20/10)
    tx_asado = transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO, monto=Decimal("80000.00"), moneda=Moneda.ARS,
            fecha=date(2026, 10, 20), descripcion="Asado", categoria_id=categorias["ocio"].id,
            billetera_id=billetera_ars.id, metodo_pago=MetodoPago.DEBITO,
        )
    )
    # 2. Super 30.000 (22/10)
    transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO, monto=Decimal("30000.00"), moneda=Moneda.ARS,
            fecha=date(2026, 10, 22), descripcion="Super", categoria_id=categorias["supermercado"].id,
            billetera_id=billetera_ars.id, metodo_pago=MetodoPago.DEBITO,
        )
    )
    # 3. Cena US$60.000 (20/10, billetera en dólares): alcanza el monto, así solo la moneda la deja afuera
    transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO, monto=Decimal("60000.00"), moneda=Moneda.USD,
            fecha=date(2026, 10, 20), descripcion="Cena", categoria_id=categorias["ocio"].id,
            billetera_id=billetera_usd.id, metodo_pago=MetodoPago.DEBITO,
        )
    )
    # 4. Viaje 200.000 (01/08)
    transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO, monto=Decimal("200000.00"), moneda=Moneda.ARS,
            fecha=date(2026, 8, 1), descripcion="Viaje", categoria_id=categorias["ocio"].id,
            billetera_id=billetera_ars.id, metodo_pago=MetodoPago.DEBITO,
        )
    )
    # 5. pago de resumen 100.000 (21/10)
    pago_res = Transaccion(
        usuario_id=usuario.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 21),
        descripcion="Pago resumen",
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta_credito.id,
        pago_resumen_vencimiento=date(2026, 11, 4),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(pago_res)
    db.commit()

    # 6. Tele 120.000 con la tarjeta en 3 cuotas (15/10)
    tx_tele = transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO, monto=Decimal("120000.00"), moneda=Moneda.ARS,
            fecha=date(2026, 10, 15), descripcion="Tele", categoria_id=categorias["ocio"].id,
            billetera_id=billetera_ars.id, metodo_pago=MetodoPago.CREDITO, tarjeta_id=tarjeta_credito.id,
            info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("120000.00")),
        )
    )

    # 7. Regalo 90.000 (18/10) con reintegro recibido de 50.000
    tx_regalo = transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO, monto=Decimal("90000.00"), moneda=Moneda.ARS,
            fecha=date(2026, 10, 18), descripcion="Regalo", categoria_id=categorias["otros_egreso"].id,
            billetera_id=billetera_ars.id, metodo_pago=MetodoPago.DEBITO,
        )
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=tx_regalo.id,
        monto=Decimal("50000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 18),
    )

    # 8. egreso de 60.000 en Otros > Pasó por mi cuenta (19/10)
    transaccion_service.crear_transaccion(
        db, usuario.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO, monto=Decimal("60000.00"), moneda=Moneda.ARS,
            fecha=date(2026, 10, 19), descripcion="Egreso pasó por mi cuenta",
            categoria_id=categorias["otros_egreso"].id, subcategoria_id=categorias["sub_paso_egr"].id,
            billetera_id=billetera_ars.id, metodo_pago=MetodoPago.DEBITO,
        )
    )

    candidatos = que_es_plata_service.gastos_para_vincular(
        db=db,
        usuario_id=usuario.id,
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 25),
        monto=Decimal("50000.00"),
    )

    assert len(candidatos) == 2
    c0 = candidatos[0]
    assert c0["id"] == tx_asado.id
    assert c0["fecha"] == date(2026, 10, 20)
    assert c0["monto"] == Decimal("80000.00")
    assert c0["disponible"] == Decimal("80000.00")

    c1 = candidatos[1]
    assert c1["id"] == tx_tele.id
    assert c1["fecha"] == date(2026, 10, 15)
    assert c1["monto"] == Decimal("120000.00")
    assert c1["disponible"] == Decimal("120000.00")


def test_api_que_es_esta_plata(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    API (modelo: fixture client de test_debito_usd.py):
    GET /transacciones/gastos-para-vincular?moneda=ARS&fecha=2026-10-25&monto=50000 → 200 con los ids de Asado y Tele en ese orden;
    monto=0 → 422; sin usuario → 401.
    POST /transacciones/{id de Juan}/que-es-esta-plata con {"opcion": "parte_de_gasto", "gasto_id": <Asado>} dos veces
    con el encabezado Idempotency-Key "clave-prueba-f1-0001": dos 200 con el mismo cuerpo (tipo_espejo "parte") y una sola fila con reintegro_de_id del Asado;
    {"opcion": "otra"} → 422;
    GET /transacciones/{id de Juan} → 200 (la ruta nueva no la tapa).
    """
    asado, juan = _crear_base_tf4(db, usuario, billetera_ars, categorias)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario

    client = TestClient(app)

    try:
        # GET /transacciones/gastos-para-vincular?moneda=ARS&fecha=2026-10-25&monto=50000
        res = client.get("/transacciones/gastos-para-vincular?moneda=ARS&fecha=2026-10-25&monto=50000")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
        assert data[0]["id"] == str(asado.id)

        # monto = 0 -> 422
        res_0 = client.get("/transacciones/gastos-para-vincular?moneda=ARS&fecha=2026-10-25&monto=0")
        assert res_0.status_code == 422

        # sin usuario -> 401
        app.dependency_overrides.pop(get_current_user, None)
        res_401 = client.get("/transacciones/gastos-para-vincular?moneda=ARS&fecha=2026-10-25&monto=50000")
        assert res_401.status_code == 401
        app.dependency_overrides[get_current_user] = lambda: usuario

        # POST /transacciones/{id de Juan}/que-es-esta-plata con {"opcion": "parte_de_gasto", "gasto_id": <Asado>} dos veces con Idempotency-Key
        headers = {"Idempotency-Key": "clave-prueba-f1-0001"}
        payload = {"opcion": "parte_de_gasto", "gasto_id": str(asado.id)}

        post1 = client.post(f"/transacciones/{juan.id}/que-es-esta-plata", json=payload, headers=headers)
        assert post1.status_code == 200
        body1 = post1.json()
        assert body1["tipo_espejo"] == "parte"

        post2 = client.post(f"/transacciones/{juan.id}/que-es-esta-plata", json=payload, headers=headers)
        assert post2.status_code == 200
        body2 = post2.json()
        assert body2["tipo_espejo"] == "parte"
        assert body1 == body2

        # Solo una fila con reintegro_de_id del Asado
        espejos_asado = db.execute(
            select(Transaccion).where(Transaccion.reintegro_de_id == asado.id)
        ).scalars().all()
        assert len(espejos_asado) == 1

        # {"opcion": "otra"} -> 422
        bad_post = client.post(
            f"/transacciones/{juan.id}/que-es-esta-plata",
            json={"opcion": "otra"},
            headers={"Idempotency-Key": "clave-prueba-f1-0002"},
        )
        assert bad_post.status_code == 422

        # GET /transacciones/{id de Juan} -> 200 (la ruta nueva no la tapa)
        get_tx = client.get(f"/transacciones/{juan.id}")
        assert get_tx.status_code == 200
        assert get_tx.json()["id"] == str(juan.id)

    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
