# -*- coding: utf-8 -*-
"""
tests/test_cobro_suscripcion_credito.py

Pruebas para el cobro de suscripciones con tarjeta de crédito:
- El cobro con tarjeta crea el padre con estado_verificacion = CONFIRMADA.
- condicion_gasto lo cuenta en su fecha (ej. $11.999 el 05/10 suma al gasto de octubre).
- etiqueta_movimiento le asigna 'Gasto' y cuenta_como 'gasto'.
- El cobro con débito sigue igual (CONFIRMADA).
- Script confirmar_cobros_suscripcion_credito.py:
  * --simular no cambia nada.
  * --aplicar cambia solo las filas correspondientes y escribe el archivo de respaldo.
  * --revertir las deja exactamente como estaban (comparando fila por fila).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria, EstadoCategoria
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.suscripcion import Suscripcion, FrecuenciaSuscripcion, EstadoSuscripcion
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import cobro_suscripcion_service
from app.services.definiciones_service import cargar_contexto, condicion_gasto, etiqueta_movimiento
from scripts.datos.confirmar_cobros_suscripcion_credito import (
    ejecutar_aplicar,
    ejecutar_revertir,
    ejecutar_simulacion,
)


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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.core.database import Base
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def usuario_demo(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email="test_susc@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        password_hash="dummy_hash",
        nombre="Test",
        apellido="Suscripcion",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(u)
    db.commit()
    return u


@pytest.fixture
def categoria_servicios(db: Session) -> Categoria:
    c = Categoria(
        id=uuid4(),
        nombre="Servicios",
        tipo=TipoCategoria.EGRESO,
        icono="film",
        color="#10b981",
        orden=1,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(c)
    db.commit()
    return c


@pytest.fixture
def tarjeta_credito(db: Session, usuario_demo: Usuario) -> TarjetaCredito:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        nombre="Billetera Tarjeta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b)
    db.flush()

    tc = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        billetera_id=b.id,
        nombre="Visa Galicia",
        moneda=Moneda.ARS,
        limite_credito=Decimal("500000.00"),
        dia_cierre=20,
        dia_vencimiento=10,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tc)
    db.commit()
    return tc


def test_cobro_tarjeta_crea_padre_confirmada_y_condicion_gasto(
    db: Session, usuario_demo: Usuario, categoria_servicios: Categoria, tarjeta_credito: TarjetaCredito
):
    """
    Verifica que el cobro de una suscripción con tarjeta:
    1. Crea la transacción padre con estado_verificacion = CONFIRMADA.
    2. condicion_gasto cuenta el monto en el mes de su fecha ($11.999 cobrado el 05/10).
    3. etiqueta_movimiento devuelve ('Gasto', 'gasto', None).
    """
    f_cobro = date(2026, 10, 5)
    monto_susc = Decimal("11999.00")

    susc = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        tarjeta_id=tarjeta_credito.id,
        categoria_id=categoria_servicios.id,
        nombre="Netflix",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=f_cobro,
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(susc)
    db.flush()

    precio = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=susc.id,
        monto=monto_susc,
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    db.add(precio)
    db.commit()

    # Ejecutar cobro
    hoy_sim = date(2026, 10, 15)
    ok = cobro_suscripcion_service._cobrar_suscripcion(db, susc, hoy=hoy_sim)
    assert ok is True
    db.commit()

    # 1. Verificar transacción padre
    tx_padre = db.execute(
        select(Transaccion).where(
            Transaccion.suscripcion_id == susc.id,
            Transaccion.es_padre_cuotas.is_(True),
        )
    ).scalar_one()

    assert tx_padre.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert tx_padre.monto == monto_susc
    assert tx_padre.metodo_pago == MetodoPago.CREDITO

    # 2. Verificar que condicion_gasto lo cuenta en octubre de 2026
    desde_oct = date(2026, 10, 1)
    hasta_oct = date(2026, 10, 31)
    cond = condicion_gasto(usuario_demo.id, desde=desde_oct, hasta=hasta_oct, hoy=hoy_sim, moneda=Moneda.ARS)

    txs_gasto = db.execute(select(Transaccion).where(cond)).scalars().all()
    gastos_ids = [t.id for t in txs_gasto]
    assert tx_padre.id in gastos_ids
    total_gasto = sum(t.monto for t in txs_gasto)
    assert total_gasto == monto_susc

    # 3. Verificar etiqueta_movimiento
    ctx = cargar_contexto(db, usuario_demo.id, hoy=hoy_sim)
    etiqueta, cuenta_como, aclaracion = etiqueta_movimiento(tx_padre, ctx)
    assert etiqueta == "Gasto"
    assert cuenta_como == "gasto"
    assert aclaracion is None


def test_cobro_debito_sigue_confirmada(
    db: Session, usuario_demo: Usuario, categoria_servicios: Categoria
):
    """Verifica que el cobro con débito sigue funcionando con estado CONFIRMADA y afectando saldo."""
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        nombre="Caja de Ahorro",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000.00"),
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    db.add(b)
    db.flush()

    f_cobro = date(2026, 10, 5)
    monto_susc = Decimal("5000.00")

    susc = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        billetera_id=b.id,
        categoria_id=categoria_servicios.id,
        nombre="Spotify",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=f_cobro,
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(susc)
    db.flush()

    precio = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=susc.id,
        monto=monto_susc,
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    db.add(precio)
    db.commit()

    ok = cobro_suscripcion_service._cobrar_suscripcion(db, susc, hoy=date(2026, 10, 15))
    assert ok is True
    db.commit()

    tx = db.execute(select(Transaccion).where(Transaccion.suscripcion_id == susc.id)).scalar_one()
    assert tx.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert tx.metodo_pago == MetodoPago.DEBITO
    db.refresh(b)
    assert b.saldo_actual == Decimal("45000.00")


def test_script_simular_no_modifica_filas(
    db: Session, usuario_demo: Usuario, tarjeta_credito: TarjetaCredito, categoria_servicios: Categoria
):
    """Verifica que --simular solo lee y no modifica el estado de las transacciones."""
    susc = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        tarjeta_id=tarjeta_credito.id,
        categoria_id=categoria_servicios.id,
        nombre="Antigua Susc",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 9, 1),
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(susc)
    db.flush()

    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        tipo=cobro_suscripcion_service.TipoTransaccion.EGRESO,
        monto=Decimal("8000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 1),
        descripcion="Antigua Susc",
        categoria_id=categoria_servicios.id,
        billetera_id=tarjeta_credito.billetera_id,
        tarjeta_id=tarjeta_credito.id,
        metodo_pago=MetodoPago.CREDITO,
        origen=cobro_suscripcion_service.OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        suscripcion_id=susc.id,
        es_cuota_hija=False,
        es_padre_cuotas=True,
    )
    db.add(tx)
    db.commit()

    res = ejecutar_simulacion(db)
    assert res["total_transacciones"] == 1

    # Sigue pendiente
    db.refresh(tx)
    assert tx.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE


def test_script_aplicar_y_revertir(
    db: Session, usuario_demo: Usuario, tarjeta_credito: TarjetaCredito, categoria_servicios: Categoria, tmp_path
):
    """
    Verifica que:
    1. --aplicar actualiza solo las filas coincidentes a CONFIRMADA y genera el JSON de backup.
    2. --revertir devuelve exactamente esas filas a PENDIENTE, comparando fila por fila.
    """
    susc = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        tarjeta_id=tarjeta_credito.id,
        categoria_id=categoria_servicios.id,
        nombre="Susc 1",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 8, 1),
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(susc)
    db.flush()

    tx1 = Transaccion(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        tipo=cobro_suscripcion_service.TipoTransaccion.EGRESO,
        monto=Decimal("4500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 1),
        descripcion="Susc 1",
        categoria_id=categoria_servicios.id,
        billetera_id=tarjeta_credito.billetera_id,
        tarjeta_id=tarjeta_credito.id,
        metodo_pago=MetodoPago.CREDITO,
        origen=cobro_suscripcion_service.OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        suscripcion_id=susc.id,
        es_cuota_hija=False,
        es_padre_cuotas=True,
    )
    tx_ajena = Transaccion(
        id=uuid4(),
        usuario_id=usuario_demo.id,
        tipo=cobro_suscripcion_service.TipoTransaccion.EGRESO,
        monto=Decimal("15000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 5),
        descripcion="Compra normal",
        categoria_id=categoria_servicios.id,
        billetera_id=tarjeta_credito.billetera_id,
        tarjeta_id=tarjeta_credito.id,
        metodo_pago=MetodoPago.CREDITO,
        origen=cobro_suscripcion_service.OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        suscripcion_id=None,
        es_cuota_hija=False,
        es_padre_cuotas=True,
    )
    db.add_all([tx1, tx_ajena])
    db.commit()

    backup_file = tmp_path / "backup_test.json"
    ejecutar_aplicar(db, backup_path=backup_file)

    # tx1 debe estar confirmada
    db.refresh(tx1)
    assert tx1.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA

    # tx_ajena NO debe haber sido tocada
    db.refresh(tx_ajena)
    assert tx_ajena.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE

    # Archivo de respaldo existe y contiene los datos de tx1
    assert backup_file.exists()
    with open(backup_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data) == 1
    assert data[0]["id"] == str(tx1.id)
    assert data[0]["estado_anterior"] == "pendiente"

    # Revertir
    rev_count = ejecutar_revertir(db, backup_file)
    assert rev_count == 1

    db.refresh(tx1)
    assert tx1.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
    db.refresh(tx_ajena)
    assert tx_ajena.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
