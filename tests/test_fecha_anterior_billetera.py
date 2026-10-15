"""
Pruebas para movimientos con fecha anterior a la creación de la billetera.
Ubicación: tests/test_fecha_anterior_billetera.py
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
)
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import historial_service, transaccion_service, transferencia_service


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
                    value = UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    sqlite3.register_adapter(UUID, lambda u: u.hex)
    yield


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
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
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def fixtures_caso(db_session: Session, monkeypatch):
    """
    Fixture base para cada caso:
    - Hoy: 2026-10-15
    - Billetera A creada el 2026-10-01 a las 12:00 de Argentina con saldo_inicial 100.000 (ARS)
    - Billetera B creada el 2026-09-01 con saldo_inicial 50.000 (ARS)
    """
    hoy_fijo = date(2026, 10, 15)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy_fijo)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: hoy_fijo)
    monkeypatch.setattr("app.services.historial_service.hoy_argentina", lambda: hoy_fijo)

    usuario = Usuario(
        id=uuid4(),
        email="test_fecha_ant@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Usuario",
        apellido="Prueba",
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db_session.add(usuario)
    db_session.flush()

    # Billetera A: creada el 2026-10-01 a las 12:00 de Argentina (15:00 UTC)
    fc_a = datetime(2026, 10, 1, 15, 0, 0, tzinfo=timezone.utc)
    billetera_a = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera A",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        es_efectivo=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=fc_a,
    )

    # Billetera B: creada el 2026-09-01 con saldo_inicial 50.000 (15:00 UTC)
    fc_b = datetime(2026, 9, 1, 15, 0, 0, tzinfo=timezone.utc)
    billetera_b = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera B",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        es_efectivo=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=fc_b,
    )

    db_session.add_all([billetera_a, billetera_b])
    db_session.flush()

    tarjeta_credito = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_a.id,
        nombre="Visa A",
        limite_credito=Decimal("500000.00"),
        dia_cierre=20,
        dia_vencimiento=5,
        red=RedTarjeta.VISA,
        estado=EstadoTarjeta.ACTIVA,
        moneda=Moneda.ARS,
    )
    db_session.add(tarjeta_credito)

    cat_servicios = Categoria(
        id=uuid4(),
        nombre="Servicios",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db_session.add(cat_servicios)
    db_session.commit()

    return {
        "usuario": usuario,
        "a": billetera_a,
        "b": billetera_b,
        "categoria": cat_servicios,
        "tarjeta": tarjeta_credito,
    }


def test_caso_1_egreso_anterior_a(db_session: Session, fixtures_caso):
    """egreso de 5.000 en A con fecha 2026-09-20: A.saldo_actual 100000.00 y A.saldo_inicial 105000.00."""
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Gasto 20/09",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b_a.id,
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("105000.00")


def test_caso_2_egreso_posterior_a(db_session: Session, fixtures_caso):
    """egreso de 5.000 en A con fecha 2026-10-02: A.saldo_actual 95000.00 y A.saldo_inicial 100000.00."""
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 2),
        descripcion="Gasto 02/10",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b_a.id,
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("95000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")


def test_caso_3_egreso_mismo_dia_creacion_a(db_session: Session, fixtures_caso):
    """egreso de 5.000 en A con fecha 2026-10-01 (mismo día de la creación): A.saldo_actual 95000.00 y A.saldo_inicial 100000.00."""
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 1),
        descripcion="Gasto 01/10",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b_a.id,
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("95000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")


def test_caso_4_ingreso_anterior_a(db_session: Session, fixtures_caso):
    """ingreso de 20.000 en A con fecha 2026-09-25: A.saldo_actual 100000.00 y A.saldo_inicial 80000.00."""
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 25),
        descripcion="Ingreso 25/09",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        billetera_id=b_a.id,
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("80000.00")


def test_caso_5_egreso_editado_y_eliminado(db_session: Session, fixtures_caso):
    """
    egreso del 2026-09-20 editado a fecha 2026-10-05: A.saldo_actual 95000.00 y A.saldo_inicial 100000.00.
    Si después se elimina: A.saldo_actual 100000.00 y A.saldo_inicial 100000.00.
    """
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Gasto para editar",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b_a.id,
    )
    tx = transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)

    # Editar a 2026-10-05
    update_data = TransaccionUpdate(fecha=date(2026, 10, 5))
    transaccion_service.actualizar_transaccion(db_session, usuario.id, tx.id, update_data)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("95000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")

    # Eliminar
    transaccion_service.eliminar_transaccion(db_session, usuario.id, tx.id)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")


def test_caso_6_egreso_cambiado_de_billetera(db_session: Session, fixtures_caso):
    """
    egreso del 2026-09-20 pasado de A a B (misma fecha):
    A vuelve a 100000.00 / 100000.00 y B queda en 45000.00 de saldo_actual con 50000.00 de saldo_inicial.
    """
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    b_b = fixtures_caso["b"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Gasto para mover de cuenta",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b_a.id,
    )
    tx = transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)

    # Mover a B
    update_data = TransaccionUpdate(billetera_id=b_b.id)
    transaccion_service.actualizar_transaccion(db_session, usuario.id, tx.id, update_data)
    db_session.refresh(b_a)
    db_session.refresh(b_b)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")
    assert b_b.saldo_actual == Decimal("45000.00")
    assert b_b.saldo_inicial == Decimal("50000.00")


def test_caso_7_transferencia_entre_billeteras_distinta_antiguedad(db_session: Session, fixtures_caso):
    """
    transferencia de 10.000 de A a B con fecha 2026-09-15:
    A.saldo_actual 100000.00 y A.saldo_inicial 110000.00;
    B.saldo_actual 60000.00 y B.saldo_inicial 50000.00.
    Al eliminarla, todo vuelve a los valores de arranque.
    """
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    b_b = fixtures_caso["b"]

    tr_data = TransferenciaInternaCreate(
        billetera_origen_id=b_a.id,
        billetera_destino_id=b_b.id,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 15),
    )
    tr = transferencia_service.crear_transferencia(db_session, usuario.id, tr_data)
    db_session.refresh(b_a)
    db_session.refresh(b_b)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("110000.00")
    assert b_b.saldo_actual == Decimal("60000.00")
    assert b_b.saldo_inicial == Decimal("50000.00")

    # Eliminar transferencia
    transferencia_service.eliminar_transferencia(db_session, usuario.id, tr.id)
    db_session.refresh(b_a)
    db_session.refresh(b_b)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")
    assert b_b.saldo_actual == Decimal("50000.00")
    assert b_b.saldo_inicial == Decimal("50000.00")


def test_caso_8_egreso_pendiente_y_confirmado(db_session: Session, fixtures_caso):
    """
    egreso pendiente de 5.000 en A con fecha 2026-09-20 que después se confirma con confirmar_transaccion_ia:
    A.saldo_actual 100000.00 y A.saldo_inicial 105000.00.
    """
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Gasto pendiente IA",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b_a.id,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        origen=OrigenTransaccion.IA_WPP,
    )
    tx = transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)
    db_session.refresh(b_a)

    # Mientras esté pendiente no altera saldos
    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")

    # Confirmar
    transaccion_service.confirmar_transaccion_ia(db_session, usuario.id, tx.id)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("105000.00")


def test_caso_9_compra_tarjeta_credito(db_session: Session, fixtures_caso):
    """compra con tarjeta (crédito) con fecha 2026-09-20: A no cambia (100000.00 / 100000.00)."""
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]
    tarjeta = fixtures_caso["tarjeta"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("15000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Compra con crédito",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        billetera_id=b_a.id,
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)
    db_session.refresh(b_a)

    assert b_a.saldo_actual == Decimal("100000.00")
    assert b_a.saldo_inicial == Decimal("100000.00")


def test_caso_10_historial_modo_billetera(db_session: Session, fixtures_caso):
    """
    historial de A en modo billetera después del primer caso:
    ítem "Saldo inicial" con fecha 2026-09-20, monto 105000.00,
    aclaración exacta "Con lo que arranca el historial de esta billetera";
    el último saldo_despues es 100000.00.
    """
    usuario = fixtures_caso["usuario"]
    b_a = fixtures_caso["a"]
    cat = fixtures_caso["categoria"]

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Gasto 20/09",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=b_a.id,
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data)
    db_session.refresh(b_a)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="billetera",
        billetera_id=b_a.id,
        hoy=date(2026, 10, 15),
    )

    item_si = next(it for it in res.items if it.clase == "saldo_inicial")
    assert item_si.fecha == date(2026, 9, 20)
    assert item_si.monto == Decimal("105000.00")
    assert item_si.aclaracion == "Con lo que arranca el historial de esta billetera"

    # En orden cronológico el último saldo_despues es 100000.00
    items_crono = sorted(res.items, key=lambda x: (x.fecha, 0 if x.clase == "saldo_inicial" else 1))
    assert items_crono[-1].saldo_despues == Decimal("100000.00")
