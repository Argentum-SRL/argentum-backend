"""
tests/test_reintegros.py — Pruebas para la fase5c_reintegros (backend).

Casos y valores exactos especificados en el prompt:
a) Débito, billetera con saldo 1.000.000. Compra Supermercado 100.000 el 05/10 y reintegro 25.000 recibido el 05/10:
   saldo 925.000, saldo teórico == saldo, gasto de Supermercado en el período 75.000 e ingresos sin cambio.
b) Igual pero pendiente: saldo 900.000 y gasto 100.000. Después recibido el 10/10: saldo 925.000.
c) Crédito en 1 pago 100.000 el 05/10 con reintegro 25.000 recibido el 05/10:
   resumen 04/11 con monto_facturado 75.000 y deuda 75.000; gasto 75.000; las billeteras no cambian.
d) Crédito, el resumen 04/11 ya tiene pago confirmado y el reintegro de 25.000 llega el 20/10:
   el espejo va al resumen 04/12 (cuota -25.000 con vencimiento 04/12).
e) Historial: compra y reintegro en el mismo rango = una fila de 75.000 con reintegro.estado "recibido" y totales gastos 75.000.
   Rango que solo incluye el reintegro: fila "Reintegro", sentido "entra", monto 25.000 y totales gastos -25.000.
f) DELETE reintegro sobre a): saldo 900.000, gasto 100.000 y espejo borrado.
g) Borrar la compra de a): espejo borrado y saldo 1.000.000.
h) Los tres textos de validación de D6 y el de actualizar_transaccion.
i) Compra pendiente del 01/09: notificar con hoy 01/10 crea 1 notificación con el texto exacto.
   Correr otra vez da 1. Con hoy 30/09 da 0. Al pasar a recibido, la notificación queda archivada.
j) Billetera con punto de partida 10/10, compra 05/10:
   reintegro recibido el 12/10 suma 25.000 al saldo; recibido el 08/10 no lo mueve.
k) movimientos_elegibles y el clasificador no incluyen espejos.
l) Compra en USD de una billetera en dólares: el espejo queda en USD en esa billetera.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import InfoCuotas, TransaccionCreate, TransaccionUpdate
from app.services import (
    calendario_tarjeta_service,
    historial_service,
    reintegro_service,
    resumen_tarjeta_service,
    transaccion_service,
)
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.definiciones_service import cargar_contexto, condicion_gasto
from app.utils.finanzas import clasificar_gastos
from app.utils.patrones import movimientos_elegibles


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
        email="test_reintegros@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="categoria_supermercado")
def categoria_fixture(db: Session, usuario: Usuario):
    cat = Categoria(
        id=uuid4(),
        nombre="Supermercado",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


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


@pytest.fixture(name="tarjeta_credito")
def tarjeta_fixture(db: Session, usuario: Usuario, billetera_ars: Billetera):
    # Cierre 22/10 y vencimiento 04/11, y siguiente 04/12
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


def test_caso_a_debito_reintegro_recibido(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """a) Débito, billetera con saldo 1.000.000. Compra Supermercado 100.000 el 05/10 y reintegro 25.000 recibido el 05/10:
       saldo 925.000, saldo teórico == saldo, gasto de Supermercado en el período 75.000 e ingresos sin cambio.
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Coto",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 5),
    )

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("925000.00")
    teorico = calcular_saldo_teorico(db, billetera_ars.id)
    assert teorico == billetera_ars.saldo_actual

    # Gasto de Supermercado en el período
    ctx = cargar_contexto(db, usuario.id, hoy=date(2026, 10, 21))
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21)),
        Transaccion.categoria_id == categoria_supermercado.id,
    )
    txs_gasto = db.execute(stmt_gasto).scalars().all()
    # compra (100.000) + espejo (-25.000) = 75.000
    total_gasto = sum(t.monto for t in txs_gasto)
    assert total_gasto == Decimal("75000.00")

    # Ingresos sin cambio (0)
    stmt_ingreso = select(Transaccion).where(
        Transaccion.usuario_id == usuario.id,
        Transaccion.tipo == TipoTransaccion.INGRESO,
    )
    assert len(db.execute(stmt_ingreso).scalars().all()) == 0


def test_caso_b_debito_reintegro_pendiente_y_luego_recibido(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """b) Igual pero pendiente: saldo 900.000 y gasto 100.000. Después recibido el 10/10: saldo 925.000."""
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Disco",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    # Reintegro pendiente
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=False,
    )

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("900000.00")

    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21)),
        Transaccion.categoria_id == categoria_supermercado.id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("100000.00")

    # Pasa a recibido el 10/10
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 10),
    )

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("925000.00")


def test_caso_c_credito_reintegro_mismo_resumen(db: Session, usuario: Usuario, billetera_ars: Billetera, tarjeta_credito: TarjetaCredito, categoria_supermercado: Categoria):
    """c) Crédito en 1 pago 100.000 el 05/10 con reintegro 25.000 recibido el 05/10:
       resumen 04/11 con monto_facturado 75.000 y deuda 75.000; gasto 75.000; las billeteras no cambian.
    """
    saldo_inicial_billetera = billetera_ars.saldo_actual

    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Jumbo",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta_credito.id,
        billetera_id=billetera_ars.id,
        info_cuotas=InfoCuotas(cantidad_cuotas=1, monto_total=Decimal("100000.00")),
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 5),
    )

    # Las billeteras no cambian
    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == saldo_inicial_billetera

    # Resumen 04/11 con monto_facturado 75.000 y deuda 75.000
    from app.services.tarjeta_service import obtener_detalle_resumen_vencimiento
    detalle = obtener_detalle_resumen_vencimiento(db, tarjeta_credito.id, date(2026, 11, 4))
    assert detalle["monto_facturado"] == Decimal("75000.00")
    assert detalle["monto_deuda_restante"] == Decimal("75000.00")

    resumen = resumen_tarjeta_service.calcular_resumen_actual(db, tarjeta_credito)
    assert resumen.total_comprometido_resumen_actual == Decimal("75000.00")
    assert resumen.total_a_pagar_resumen_actual == Decimal("75000.00")

    # Gasto devengado = 75.000
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21)),
        Transaccion.categoria_id == categoria_supermercado.id,
    )
    txs_gasto = db.execute(stmt_gasto).scalars().all()
    total_gasto = sum(t.monto for t in txs_gasto)
    assert total_gasto == Decimal("75000.00")


def test_caso_d_credito_resumen_ya_pagado_salta_al_siguiente(db: Session, usuario: Usuario, billetera_ars: Billetera, tarjeta_credito: TarjetaCredito, categoria_supermercado: Categoria):
    """d) Crédito, el resumen 04/11 ya tiene pago confirmado y el reintegro de 25.000 llega el 20/10:
       el espejo va al resumen 04/12 (cuota -25.000 con vencimiento 04/12).
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Vea",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta_credito.id,
        billetera_id=billetera_ars.id,
        info_cuotas=InfoCuotas(cantidad_cuotas=1, monto_total=Decimal("100000.00")),
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    # Registrar pago confirmado del resumen 04/11
    pago_resumen = Transaccion(
        usuario_id=usuario.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Pago resumen tarjeta",
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta_credito.id,
        pago_resumen_vencimiento=date(2026, 11, 4),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(pago_resumen)
    db.commit()

    # Reintegro llega el 20/10
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 20),
    )

    # Verificar que el espejo hija tiene vencimiento 04/12/2026
    espejo_hija = db.execute(
        select(Transaccion).where(
            Transaccion.reintegro_de_id == compra.id,
            Transaccion.es_cuota_hija.is_(True),
        )
    ).scalar_one()

    assert espejo_hija.fecha == date(2026, 12, 4)
    assert espejo_hija.monto == Decimal("-25000.00")

    cuota_reg = db.execute(
        select(Cuota).where(Cuota.transaccion_id == espejo_hija.id)
    ).scalar_one()
    assert cuota_reg.fecha_vencimiento == date(2026, 12, 4)
    assert cuota_reg.monto_proyectado == Decimal("-25000.00")


def test_caso_e_historial_consolidacion_y_standalone(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """e) Historial: compra y reintegro en el mismo rango = una fila de 75.000 con reintegro.estado "recibido" y totales gastos 75.000.
       Rango que solo incluye el reintegro: fila "Reintegro", sentido "entra", monto 25.000 y totales gastos -25.000.
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 15),
        descripcion="Supermercado Mayorista",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 5),
    )

    # 1. Rango que incluye compra (15/09) y reintegro (05/10): ambos en la lista
    resp_ambos = historial_service.listar_movimientos(
        db=db,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 9, 1),
        fecha_hasta=date(2026, 10, 31),
        hoy=date(2026, 10, 21),
    )

    assert len(resp_ambos.items) == 1
    fila = resp_ambos.items[0]
    assert fila.monto == Decimal("75000.00")
    assert fila.reintegro is not None
    assert fila.reintegro.estado == "recibido"
    assert fila.reintegro.monto == Decimal("25000.00")
    assert resp_ambos.totales["ARS"].gastos == Decimal("75000.00")

    # 2. Rango que SOLO incluye el reintegro (01/10 a 31/10)
    resp_solo_reintegro = historial_service.listar_movimientos(
        db=db,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        hoy=date(2026, 10, 21),
    )

    assert len(resp_solo_reintegro.items) == 1
    fila_r = resp_solo_reintegro.items[0]
    assert fila_r.etiqueta == "Reintegro"
    assert fila_r.sentido == "entra"
    assert fila_r.monto == Decimal("25000.00")
    assert fila_r.cuenta_como == "gasto"
    assert resp_solo_reintegro.totales["ARS"].gastos == Decimal("-25000.00")


def test_caso_f_delete_reintegro_revierte_saldo_y_espejo(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """f) DELETE reintegro sobre a): saldo 900.000, gasto 100.000 y espejo borrado."""
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Coto",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 5),
    )

    # Eliminar reintegro
    reintegro_service.eliminar_reintegro(db, usuario.id, compra.id)

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("900000.00")

    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21)),
        Transaccion.categoria_id == categoria_supermercado.id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("100000.00")

    # Espejo borrado
    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalars().all()
    assert len(espejos) == 0


def test_caso_g_borrar_compra_borra_espejo_y_revierte_saldo(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """g) Borrar la compra de a): espejo borrado y saldo 1.000.000."""
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Coto",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 5),
    )

    transaccion_service.eliminar_transaccion(db, usuario.id, compra.id)

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("1000000.00")

    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalars().all()
    assert len(espejos) == 0


def test_caso_h_validaciones_exactas_d6(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """h) Los tres textos de validación de D6 y el de actualizar_transaccion."""
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Coto",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    # 1. "El reintegro tiene que ser mayor a cero."
    with pytest.raises(HTTPException) as exc1:
        reintegro_service.registrar_o_actualizar_reintegro(
            db, usuario.id, compra.id, monto=Decimal("0.00"), recibido=True
        )
    assert exc1.value.detail == "El reintegro tiene que ser mayor a cero."

    # 2. "El reintegro no puede ser mayor que la compra."
    with pytest.raises(HTTPException) as exc2:
        reintegro_service.registrar_o_actualizar_reintegro(
            db, usuario.id, compra.id, monto=Decimal("100000.01"), recibido=True
        )
    assert exc2.value.detail == "El reintegro no puede ser mayor que la compra."

    # 3. "Solo un gasto puede tener reintegro."
    ingreso_data = TransaccionCreate(
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Sueldo",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        billetera_id=billetera_ars.id,
    )
    ingreso = transaccion_service.crear_transaccion(db, usuario.id, ingreso_data)
    with pytest.raises(HTTPException) as exc3:
        reintegro_service.registrar_o_actualizar_reintegro(
            db, usuario.id, ingreso.id, monto=Decimal("10000.00"), recibido=True
        )
    assert exc3.value.detail == "Solo un gasto puede tener reintegro."

    # 4. En actualizar_transaccion: "Primero quitá el reintegro para cambiar el medio de pago o la billetera."
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, compra.id, monto=Decimal("25000.00"), recibido=True
    )
    billetera_2 = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera 2",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
    )
    db.add(billetera_2)
    db.commit()

    with pytest.raises(HTTPException) as exc4:
        transaccion_service.actualizar_transaccion(
            db, usuario.id, compra.id, TransaccionUpdate(billetera_id=billetera_2.id)
        )
    assert exc4.value.detail == "Primero quitá el reintegro para cambiar el medio de pago o la billetera."


def test_caso_i_aviso_30_dias_notificacion(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """i) Compra pendiente del 01/09: notificar con hoy 01/10 crea 1 notificación con el texto exacto.
       Correr otra vez da 1. Con hoy 30/09 da 0. Al pasar a recibido, la notificación queda archivada.
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 1),
        descripcion="Supermercado Coto",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, compra.id, monto=Decimal("25000.00"), recibido=False
    )

    # Con hoy 30/09 da 0 (29 días transcurridos)
    assert reintegro_service.notificar_reintegros_pendientes(db, date(2026, 9, 30)) == 0

    # Con hoy 01/10 crea 1 notificación
    creadas = reintegro_service.notificar_reintegros_pendientes(db, date(2026, 10, 1))
    assert creadas == 1

    notif = db.execute(
        select(Notificacion).where(Notificacion.grupo_agrupacion == f"REINTEGRO_PENDIENTE_{compra.id}")
    ).scalar_one()

    assert notif.mensaje == "¿Te llegó el reintegro de $25.000 de Supermercado Coto?"
    assert notif.deep_link == f"/app/transacciones?transaccion={compra.id}"
    assert notif.canal_web is True
    assert notif.canal_whatsapp is False
    assert notif.canal_email is False
    assert notif.archivada is False

    # Correr otra vez da 0 creadas (no duplica)
    assert reintegro_service.notificar_reintegros_pendientes(db, date(2026, 10, 1)) == 0

    # Al pasar a recibido, la notificación queda archivada
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, compra.id, monto=Decimal("25000.00"), recibido=True, fecha_recibido=date(2026, 10, 2)
    )

    db.refresh(notif)
    assert notif.archivada is True


def test_caso_j_punto_partida_reintegro(db: Session, usuario: Usuario, categoria_supermercado: Categoria):
    """j) Billetera con punto de partida 10/10, compra 05/10:
       reintegro recibido el 12/10 suma 25.000 al saldo; recibido el 08/10 no lo mueve.
    """
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Sueldo",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        fecha_saldo=date(2026, 10, 10),
    )
    db.add(billetera)
    db.commit()

    # Compra 05/10 (anterior al punto de partida, no mueve saldo_actual)
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Supermercado Coto",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)
    db.refresh(billetera)
    assert billetera.saldo_actual == Decimal("500000.00")

    # 1. Reintegro recibido el 08/10 (anterior al punto de partida): no mueve saldo_actual
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, compra.id, monto=Decimal("25000.00"), recibido=True, fecha_recibido=date(2026, 10, 8)
    )
    db.refresh(billetera)
    assert billetera.saldo_actual == Decimal("500000.00")

    # 2. Reintegro recibido el 12/10 (posterior al punto de partida): suma 25.000 al saldo
    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, compra.id, monto=Decimal("25000.00"), recibido=True, fecha_recibido=date(2026, 10, 12)
    )
    db.refresh(billetera)
    assert billetera.saldo_actual == Decimal("525000.00")


def test_caso_k_patrones_y_clasificador_excluyen_espejos(db: Session, usuario: Usuario, billetera_ars: Billetera, categoria_supermercado: Categoria):
    """k) movimientos_elegibles y el clasificador no incluyen espejos."""
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Gasto Habitual",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, compra.id, monto=Decimal("25000.00"), recibido=True, fecha_recibido=date(2026, 10, 5)
    )

    espejo = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalar_one()

    ctx = cargar_contexto(db, usuario.id, hoy=date(2026, 10, 21))

    # movimientos_elegibles
    elegibles = movimientos_elegibles([compra, espejo], ctx)
    assert compra in elegibles
    assert espejo not in elegibles

    # clasificador
    clasif = clasificar_gastos(
        transacciones=[compra, espejo],
        ciclos=[(date(2026, 10, 1), date(2026, 10, 31))],
        fecha_destino=date(2026, 10, 21),
        ipc_records={},
        ctx=ctx,
    )
    ids_en_clasif = {
        tx.id for s in clasif.streams for tx in s.transacciones
    }
    assert espejo.id not in ids_en_clasif


def test_caso_l_compra_usd_espejo_queda_en_usd(db: Session, usuario: Usuario, categoria_supermercado: Categoria):
    """l) Compra en USD de una billetera en dólares: el espejo queda en USD en esa billetera."""
    billetera_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Caja de Ahorro USD",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("1000.00"),
        saldo_actual=Decimal("1000.00"),
        fecha_saldo=date(2026, 10, 1),
    )
    db.add(billetera_usd)
    db.commit()

    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 5),
        descripcion="Amazon Purchase USD",
        categoria_id=categoria_supermercado.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_usd.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db, usuario.id, compra.id, monto=Decimal("25.00"), recibido=True, fecha_recibido=date(2026, 10, 5)
    )

    db.refresh(billetera_usd)
    assert billetera_usd.saldo_actual == Decimal("925.00")

    espejo = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalar_one()

    assert espejo.moneda == Moneda.USD
    assert espejo.monto == Decimal("-25.00")
    assert espejo.billetera_id == billetera_usd.id
