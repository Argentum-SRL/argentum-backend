"""
tests/test_disponible_dolares.py — Pruebas de disponible libre con resumen en dólares y pesificación.

Verifica:
1. Con billetera USD de US$100:
   - Ítems de Próximos pagos de 200.000 ARS y 150 USD.
   - libre USD 0.00; libre ARS 735.000,00.
   - Compromisos ARS con ítem 'dolares_con_pesos' de 65.000,00,
     nombre exacto 'Parte en dólares de Resumen <nombre> que se paga con pesos',
     monto_usd 50, cotizacion 1000 y percepcion 30.
2. Sin billetera USD:
   - libre ARS 605.000,00 y libre USD 0.00.
3. Con billetera USD de US$500:
   - libre USD 350.00 y libre ARS 800.000,00, sin ítem dolares_con_pesos.
4. Con US$100 y sin ninguna cotización cargada:
   - libre USD -50.00 y libre ARS 800.000,00.
5. Con las cuotas en dólares marcadas pagadas:
   - No hay ítem en dólares en Próximos pagos.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import pagos_proximos_service


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


def _setup_base_scenario(db: Session):
    hoy = date(2026, 10, 15)
    fecha_fin_ciclo = date(2026, 10, 31)

    # Usuario con ciclo que incluye el vencimiento
    usuario = Usuario(
        id=uuid4(),
        email="test_dolares@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)

    # Billetera ARS con $1.000.000
    billetera_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000000.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(billetera_ars)

    # Tarjeta ARS con percepcion 30 (vencimiento día 20, dentro del ciclo)
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_ars.id,
        nombre="Visa Santander",
        red=RedTarjeta.VISA,
        dia_cierre=10,
        dia_vencimiento=20,
        regla_cierre="dia_fijo",
        moneda=Moneda.ARS,
        percepcion_moneda_extranjera=Decimal("30.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)

    # Cotización oficial venta de hoy 1.000
    cotizacion = CotizacionDolar(
        fecha=hoy,
        tipo="oficial",
        compra=Decimal("950.00"),
        venta=Decimal("1000.00"),
        promedio=Decimal("975.00"),
    )
    db.add(cotizacion)

    # Transacciones y cuotas dentro del ciclo: $200.000 ARS y US$150 USD
    f_venc = date(2026, 10, 20)

    # Cuota ARS de $200.000
    tx_padre_ars = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("200000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_padre_ars)
    grupo_ars = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx_padre_ars.id,
        descripcion="Compra electrodoméstico",
        monto_total=Decimal("200000.00"),
        cantidad_cuotas=1,
        total_financiado=Decimal("200000.00"),
        moneda=Moneda.ARS,
        primer_vencimiento=f_venc,
    )
    db.add(grupo_ars)
    cuota_ars = Cuota(
        id=uuid4(),
        grupo_id=grupo_ars.id,
        transaccion_id=tx_padre_ars.id,
        numero_cuota=1,
        monto_proyectado=Decimal("200000.00"),
        monto_real=Decimal("200000.00"),
        fecha_vencimiento=f_venc,
        pagada=False,
    )
    db.add(cuota_ars)

    # Cuota USD de US$150
    tx_padre_usd = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("150.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 6),
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_padre_usd)
    grupo_usd = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx_padre_usd.id,
        descripcion="Servicio cloud USD",
        monto_total=Decimal("150.00"),
        cantidad_cuotas=1,
        total_financiado=Decimal("150.00"),
        moneda=Moneda.USD,
        primer_vencimiento=f_venc,
    )
    db.add(grupo_usd)
    cuota_usd = Cuota(
        id=uuid4(),
        grupo_id=grupo_usd.id,
        transaccion_id=tx_padre_usd.id,
        numero_cuota=1,
        monto_proyectado=Decimal("150.00"),
        monto_real=Decimal("150.00"),
        fecha_vencimiento=f_venc,
        pagada=False,
    )
    db.add(cuota_usd)

    db.commit()

    return {
        "usuario": usuario,
        "tarjeta": tarjeta,
        "cuota_ars": cuota_ars,
        "cuota_usd": cuota_usd,
        "hoy": hoy,
        "fecha_fin_ciclo": fecha_fin_ciclo,
    }


def test_con_billetera_usd_100(db: Session):
    data = _setup_base_scenario(db)
    u = data["usuario"]
    t = data["tarjeta"]
    hoy = data["hoy"]
    fin = data["fecha_fin_ciclo"]

    # Con billetera USD de US$100
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("100.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_usd)
    db.commit()

    pagos = pagos_proximos_service.listar_pagos_proximos(db, u, fin, hoy=hoy)

    # Ítems de Próximos pagos de 200.000 ARS y 150 USD
    pago_ars = next((p for p in pagos if p["moneda"] == "ARS" and p["tipo"] == "resumen_tarjeta"), None)
    pago_usd = next((p for p in pagos if p["moneda"] == "USD" and p["tipo"] == "resumen_tarjeta"), None)
    assert pago_ars is not None
    assert pago_usd is not None
    assert Decimal(str(pago_ars["monto"])) == Decimal("200000.00")
    assert Decimal(str(pago_usd["monto"])) == Decimal("150.00")

    disp = pagos_proximos_service.calcular_disponible_libre(db, u, fin, hoy=hoy, pagos=pagos)

    # libre USD 0.00; libre ARS 735000.00
    assert disp["usd"]["saldo_disponible"] == Decimal("0.00")
    assert disp["ars"]["saldo_disponible"] == Decimal("735000.00")

    # compromisos ARS con el ítem "dolares_con_pesos" de 65000.00
    compromisos_ars = disp["ars"]["compromisos"]
    item_dolares = next((c for c in compromisos_ars if c.get("tipo") == "dolares_con_pesos"), None)
    assert item_dolares is not None
    assert item_dolares["monto"] == Decimal("65000.00")

    # nombre exacto "Parte en dólares de Resumen <nombre> que se paga con pesos"
    nombre_esperado = f"Parte en dólares de Resumen {t.nombre} que se paga con pesos"
    assert item_dolares["nombre"] == nombre_esperado

    # monto_usd 50, cotizacion 1000 y percepcion 30
    assert item_dolares["monto_usd"] == 50
    assert item_dolares["cotizacion"] == 1000
    assert item_dolares["percepcion"] == 30


def test_sin_billetera_usd(db: Session):
    data = _setup_base_scenario(db)
    u = data["usuario"]
    hoy = data["hoy"]
    fin = data["fecha_fin_ciclo"]

    disp = pagos_proximos_service.calcular_disponible_libre(db, u, fin, hoy=hoy)

    # Sin billetera USD: libre ARS 605000.00 y libre USD 0.00
    assert disp["ars"]["saldo_disponible"] == Decimal("605000.00")
    assert disp["usd"]["saldo_disponible"] == Decimal("0.00")


def test_con_billetera_usd_500(db: Session):
    data = _setup_base_scenario(db)
    u = data["usuario"]
    hoy = data["hoy"]
    fin = data["fecha_fin_ciclo"]

    # Con billetera USD de US$500
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("500.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_usd)
    db.commit()

    disp = pagos_proximos_service.calcular_disponible_libre(db, u, fin, hoy=hoy)

    # Con billetera USD de US$500: libre USD 350.00 y libre ARS 800000.00, sin ítem dolares_con_pesos
    assert disp["usd"]["saldo_disponible"] == Decimal("350.00")
    assert disp["ars"]["saldo_disponible"] == Decimal("800000.00")
    assert not any(c.get("tipo") == "dolares_con_pesos" for c in disp["ars"]["compromisos"])


def test_sin_cotizacion_cargada(db: Session):
    data = _setup_base_scenario(db)
    u = data["usuario"]
    hoy = data["hoy"]
    fin = data["fecha_fin_ciclo"]

    # Con billetera USD de US$100
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("100.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_usd)

    # Sin ninguna cotización cargada
    db.query(CotizacionDolar).delete()
    db.commit()

    disp = pagos_proximos_service.calcular_disponible_libre(db, u, fin, hoy=hoy)

    # Con US$100 y sin ninguna cotización cargada: libre USD -50.00 y libre ARS 800000.00
    assert disp["usd"]["saldo_disponible"] == Decimal("-50.00")
    assert disp["ars"]["saldo_disponible"] == Decimal("800000.00")


def test_cuotas_dolares_pagadas(db: Session):
    data = _setup_base_scenario(db)
    u = data["usuario"]
    cuota_usd = data["cuota_usd"]
    hoy = data["hoy"]
    fin = data["fecha_fin_ciclo"]

    # Con las cuotas en dólares marcadas pagadas
    cuota_usd.pagada = True
    db.commit()

    pagos = pagos_proximos_service.listar_pagos_proximos(db, u, fin, hoy=hoy)

    # Con las cuotas en dólares marcadas pagadas: no hay ítem en dólares
    assert not any(p["moneda"] == "USD" for p in pagos)
