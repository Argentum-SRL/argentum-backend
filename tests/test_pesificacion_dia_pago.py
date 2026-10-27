"""
Tests de pesificación de resumen de tarjeta con el dólar oficial vendedor del día del pago (TE.8).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.fecha_resumen_tarjeta import FechaResumenTarjeta
from app.models.grupo_cuotas import GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import pago_resumen_service, resumen_tarjeta_service
from app.services.pesificacion_service import cotizacion_pesificacion


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


def _cargar_cotizaciones_te8(db: Session) -> None:
    filas = [
        CotizacionDolar(
            fecha=date(2026, 10, 10),
            tipo="oficial",
            compra=Decimal("1360.00"),
            venta=Decimal("1400.00"),
            promedio=Decimal("1380.00"),
        ),
        CotizacionDolar(
            fecha=date(2026, 10, 20),
            tipo="oficial",
            compra=Decimal("1410.00"),
            venta=Decimal("1450.00"),
            promedio=Decimal("1430.00"),
        ),
        CotizacionDolar(
            fecha=date(2026, 10, 25),
            tipo="oficial",
            compra=Decimal("1420.00"),
            venta=Decimal("1460.00"),
            promedio=Decimal("1440.00"),
        ),
        CotizacionDolar(
            fecha=date(2026, 10, 28),
            tipo="oficial",
            compra=Decimal("1430.00"),
            venta=Decimal("1470.00"),
            promedio=Decimal("1450.00"),
        ),
    ]
    db.add_all(filas)
    db.commit()


def _crear_escenario_tarjeta_usd(db: Session):
    u = Usuario(
        id=uuid4(),
        email=f"te8_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        es_principal=True,
        es_inversion=False,
        estado="activa",
    )
    db.add(b)

    cat_banco = Categoria(
        id=uuid4(),
        nombre="Banco",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat_banco)
    db.flush()

    sub_tc = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Tarjeta de crédito",
        orden=1,
    )
    sub_imp = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Impuestos",
        orden=5,
    )
    db.add_all([sub_tc, sub_imp])

    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Visa 4455",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=10,
        dia_vencimiento=25,
        limite_credito=Decimal("5000000.00"),
        percepcion_moneda_extranjera=Decimal("30.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)
    db.flush()

    override_oct = FechaResumenTarjeta(
        id=uuid4(),
        tarjeta_id=tarjeta.id,
        fecha_cierre=date(2026, 10, 10),
        fecha_vencimiento=date(2026, 10, 25),
    )
    db.add(override_oct)

    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat_banco.id,
        subcategoria_id=sub_tc.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 5),
        descripcion="Servicio exterior",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_padre)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        transaccion_padre_id=tx_padre.id,
        tarjeta_id=tarjeta.id,
        descripcion="Servicio exterior",
        monto_total=Decimal("100.00"),
        total_financiado=Decimal("100.00"),
        cantidad_cuotas=1,
        tiene_interes=False,
        moneda=Moneda.USD,
    )
    db.add(grupo)
    db.flush()

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat_banco.id,
        subcategoria_id=sub_tc.id,
        grupo_cuotas_id=grupo.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 25),
        descripcion="Servicio exterior (Cuota 1/1)",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db.add(tx_hija)
    db.flush()

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        monto_proyectado=Decimal("100.00"),
        monto_real=None,
        fecha_vencimiento=date(2026, 10, 25),
        pagada=False,
    )
    db.add(cuota)
    db.commit()
    return u, b, tarjeta, cuota


def test_te8_cotizacion_pesificacion_resolucion(db: Session):
    _cargar_cotizaciones_te8(db)

    cot_20, ref_20 = cotizacion_pesificacion(db, date(2026, 10, 20), date(2026, 10, 25))
    assert cot_20 == Decimal("1450.00")
    assert ref_20 == date(2026, 10, 20)

    cot_22, ref_22 = cotizacion_pesificacion(db, date(2026, 10, 22), date(2026, 10, 25))
    assert cot_22 == Decimal("1450.00")
    assert ref_22 == date(2026, 10, 22)

    cot_28, ref_28 = cotizacion_pesificacion(db, date(2026, 10, 28), date(2026, 10, 25))
    assert cot_28 == Decimal("1460.00")
    assert ref_28 == date(2026, 10, 25)


def test_te8_pagar_resumen_dia_pago_y_post_vencimiento(db: Session):
    _cargar_cotizaciones_te8(db)

    # Caso 1: fecha_pago 20/10/2026 -> pago $145.000,00 y percepción $43.500,00
    u1, b1, t1, _ = _crear_escenario_tarjeta_usd(db)
    tx_pago_20 = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=u1.id,
        tarjeta_id=t1.id,
        fecha_pago=date(2026, 10, 20),
        fecha_resumen=date(2026, 10, 25),
        moneda=Moneda.USD,
        billetera_id=b1.id,
        pesificar=True,
    )
    assert tx_pago_20.monto == Decimal("145000.00")
    assert getattr(tx_pago_20, "monto_percepcion") == Decimal("43500.00")

    # Caso 2: fecha_pago 28/10/2026 -> pago $146.000,00 y percepción $43.800,00
    u2, b2, t2, _ = _crear_escenario_tarjeta_usd(db)
    tx_pago_28 = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=u2.id,
        tarjeta_id=t2.id,
        fecha_pago=date(2026, 10, 28),
        fecha_resumen=date(2026, 10, 25),
        moneda=Moneda.USD,
        billetera_id=b2.id,
        pesificar=True,
    )
    assert tx_pago_28.monto == Decimal("146000.00")
    assert getattr(tx_pago_28, "monto_percepcion") == Decimal("43800.00")


def test_te8_simular_y_calcular_resumen_actual(db: Session, monkeypatch):
    _cargar_cotizaciones_te8(db)
    u, _, tarjeta, _ = _crear_escenario_tarjeta_usd(db)

    monkeypatch.setattr(resumen_tarjeta_service, "hoy_argentina", lambda: date(2026, 10, 20))
    monkeypatch.setattr(pago_resumen_service, "hoy_argentina", lambda: date(2026, 10, 20))

    sim = pago_resumen_service.simular_pesificacion(
        db=db,
        usuario_id=u.id,
        tarjeta_id=tarjeta.id,
        fecha_resumen=date(2026, 10, 25),
        fecha_pago=date(2026, 10, 20),
    )
    assert sim.cotizacion_oficial == Decimal("1450.00")
    assert sim.monto_convertido_ars == Decimal("145000.00")
    assert sim.monto_percepcion_ars == Decimal("43500.00")
    assert sim.total_estimado_ars == Decimal("188500.00")

    res = resumen_tarjeta_service.calcular_resumen_actual(db, tarjeta)
    bloque_usd = res.totales_por_moneda["USD"]
    assert bloque_usd.cotizacion_oficial_estimada == Decimal("1450.00")
    assert bloque_usd.total_estimado_ars == Decimal("188500.00")


def test_te8_sin_cotizacion_error_exacto(db: Session):
    db.execute(delete(CotizacionDolar))
    db.commit()

    u, b, tarjeta, _ = _crear_escenario_tarjeta_usd(db)
    with pytest.raises(HTTPException) as exc_info:
        pago_resumen_service.pagar_resumen_tarjeta(
            db=db,
            usuario_id=u.id,
            tarjeta_id=tarjeta.id,
            fecha_pago=date(2026, 10, 20),
            fecha_resumen=date(2026, 10, 25),
            moneda=Moneda.USD,
            billetera_id=b.id,
            pesificar=True,
        )
    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == "No hay dólar oficial cargado para el 20/10/2026. Ingresá la cotización a mano."
    )
