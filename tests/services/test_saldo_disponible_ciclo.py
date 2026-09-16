"""
Tests para calcular_saldo_disponible_ciclo_actual en dashboard_service.py.
Verifica que todos los cálculos monetarios sean Decimal de punta a punta,
la correcta exclusión de cuotas cubiertas por resumen, y la inclusión de compromisos y suscripciones.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta, RedTarjeta
from app.models.transaccion import Transaccion, TipoTransaccion, MetodoPago, OrigenTransaccion, EstadoVerificacionTransaccion
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.cuota import Cuota
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.services.dashboard_service import calcular_saldo_disponible_ciclo_actual


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)


def test_calcular_saldo_disponible_decimal_y_reglas(db_session):
    # 1. Crear usuario
    usuario = Usuario(
        id=uuid4(),
        email="test_disponible@argentum.com",
        password_hash="hash",
        nombre="Test",
        apellido="User",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
    )
    db_session.add(usuario)
    db_session.commit()

    # 2. Crear Billeteras
    b_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA
    )
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera USD",
        moneda=Moneda.USD,
        saldo_actual=Decimal("500.00"),
        estado=EstadoBilletera.ACTIVA
    )
    db_session.add_all([b_ars, b_usd])
    db_session.commit()

    # 3. Crear Tarjeta
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_ars.id,
        nombre="Visa Gold",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=10,
        limite_credito=Decimal("500000.00"),
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA
    )
    db_session.add(tarjeta)
    db_session.commit()

    # 4. Crear Transacción Padre y Grupo de Cuotas
    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 1),
        descripcion="Compra financiada",
        billetera_id=b_ars.id,
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx_padre)
    db_session.commit()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        transaccion_padre_id=tx_padre.id,
        tarjeta_id=tarjeta.id,
        descripcion="3 cuotas sin interes",
        monto_total=Decimal("30000.00"),
        cantidad_cuotas=3,
        total_financiado=Decimal("30000.00"),
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
    )
    db_session.add(grupo)
    db_session.commit()

    # Cuota 1: ya vencida pero CUBIERTA por pago de resumen
    c1 = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_padre.id,
        numero_cuota=1,
        monto_proyectado=Decimal("10000.00"),
        fecha_vencimiento=date(2026, 9, 10),
        pagada=False,
    )
    # Cuota 2: vence en el ciclo actual (NO cubierta por resumen)
    c2 = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_padre.id,
        numero_cuota=2,
        monto_proyectado=Decimal("10000.00"),
        fecha_vencimiento=date(2026, 9, 25),
        pagada=False,
    )
    # Cuota 3: vence fuera del ciclo (ciclo termina 2026-09-30)
    c3 = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_padre.id,
        numero_cuota=3,
        monto_proyectado=Decimal("10000.00"),
        fecha_vencimiento=date(2026, 10, 10),
        pagada=False,
    )
    db_session.add_all([c1, c2, c3])
    db_session.commit()

    # Transacción de pago de resumen que cubre cuotas hasta 2026-09-10
    tx_pago_resumen = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 10),
        descripcion="Resumen Tarjeta Septiembre",
        billetera_id=b_ars.id,
        tarjeta_id=tarjeta.id,
        pago_resumen_vencimiento=date(2026, 9, 10),
        origen=OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE
    )
    db_session.add(tx_pago_resumen)
    db_session.commit()

    # 5. Suscripción activa en el ciclo
    sub1 = Suscripcion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_ars.id,
        nombre="Servicio Streaming",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        estado=EstadoSuscripcion.ACTIVA,
        proximo_cobro=date(2026, 9, 20)
    )
    db_session.add(sub1)
    db_session.commit()

    hist1 = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=sub1.id,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    db_session.add(hist1)
    db_session.commit()

    # Suscripción fuera del ciclo (octubre)
    sub2 = Suscripcion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_ars.id,
        nombre="Servicio Musica",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        estado=EstadoSuscripcion.ACTIVA,
        proximo_cobro=date(2026, 10, 5)
    )
    db_session.add(sub2)
    db_session.commit()

    hist2 = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=sub2.id,
        monto=Decimal("3000.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 1, 1),
    )
    db_session.add(hist2)
    db_session.commit()

    # Ejecutar cálculo para el ciclo 2026-09-01 al 2026-09-30
    res = calcular_saldo_disponible_ciclo_actual(
        db=db_session,
        usuario=usuario,
        fecha_fin_ciclo=date(2026, 9, 30),
        fecha_inicio_ciclo=date(2026, 9, 1)
    )

    # Verificaciones ARS
    ars = res["ars"]
    assert isinstance(ars["saldo_total"], Decimal)
    assert isinstance(ars["cuotas_pendientes"], Decimal)
    assert isinstance(ars["suscripciones_pendientes"], Decimal)
    assert isinstance(ars["saldo_disponible"], Decimal)
    assert "otros_compromisos" not in ars

    assert ars["saldo_total"] == Decimal("100000.00")
    # c1 está cubierta por tx_pago_resumen (vencimiento <= 2026-09-10) -> excluida.
    # c2 vence el 2026-09-25 -> incluida (10000.00).
    # c3 vence en octubre -> excluida.
    assert ars["cuotas_pendientes"] == Decimal("10000.00")
    # sub1 (5000.00) incluida, sub2 (octubre) excluida
    assert ars["suscripciones_pendientes"] == Decimal("5000.00")
    # Saldo disponible = 100000 - (10000 + 5000) = 85000.00
    assert ars["saldo_disponible"] == Decimal("85000.00")

    # Verificaciones USD
    usd = res["usd"]
    assert usd["saldo_total"] == Decimal("500.00")
    assert usd["cuotas_pendientes"] == Decimal("0.00")
    assert usd["suscripciones_pendientes"] == Decimal("0.00")
    assert "otros_compromisos" not in usd
    assert usd["saldo_disponible"] == Decimal("500.00")

    # Filtrar solo por billetera USD
    res_solo_usd = calcular_saldo_disponible_ciclo_actual(
        db=db_session,
        usuario=usuario,
        fecha_fin_ciclo=date(2026, 9, 30),
        fecha_inicio_ciclo=date(2026, 9, 1),
        billetera_ids=[b_usd.id]
    )
    assert res_solo_usd["ars"]["saldo_total"] == Decimal("0.00")
    assert res_solo_usd["ars"]["saldo_disponible"] == Decimal("0.00")
    assert res_solo_usd["usd"]["saldo_total"] == Decimal("500.00")
    assert res_solo_usd["usd"]["saldo_disponible"] == Decimal("500.00")
