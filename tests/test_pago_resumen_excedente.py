"""
Tests de pago de resumen con excedente (Fase E1 Pago).
Verifica:
1. Pago mayor al total con cargos_banco -> pago por total + egreso por excedente (Banco > Impuestos).
2. Pago mayor al total con compras_no_cargadas -> egreso con categoría y subcategoría elegidas.
3. compras_no_cargadas sin categoría -> 400 "Elegí la categoría de las compras que no cargaste."
4. Eliminar el pago -> se borra también el egreso del excedente y se restaura el saldo.
5. Con pesificación y monto mayor al total -> 400 (no permitido pagar más).
6. Pago menor o igual al total -> comportamiento habitual sin egreso extra.
7. Cómputo de gasto según definiciones_service -> incluye los cargos bancarios del excedente y excluye el pago de resumen.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select
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
from app.services import definiciones_service, pago_resumen_service, transaccion_service


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


def _crear_escenario_base(db: Session):
    """
    Crea usuario, billetera con saldo $1.000.000, tarjeta en pesos terminada en 5077,
    categorías Banco (con Impuestos y Tarjeta de crédito) y Supermercado,
    y un resumen de total $480.794,89.
    """
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Sueldo",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        es_inversion=False,
    )
    db.add(b)

    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Visa Galicia 5077",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=20,
        dia_vencimiento=5,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(t)

    cat_banco = Categoria(
        id=uuid4(),
        nombre="Banco",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat_banco)

    subcat_tc = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Tarjeta de crédito",
        orden=1,
    )
    db.add(subcat_tc)

    subcat_impuestos = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Impuestos",
        orden=10,
    )
    db.add(subcat_impuestos)

    cat_super = Categoria(
        id=uuid4(),
        nombre="Supermercado",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat_super)

    subcat_alim = Subcategoria(
        id=uuid4(),
        categoria_id=cat_super.id,
        nombre="Alimentos",
        orden=1,
    )
    db.add(subcat_alim)

    # Compra y cuota para generar total de resumen $480.794,89 con vencimiento 2026-10-05
    vto_resumen = date(2026, 10, 5)
    tx_compra = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tarjeta_id=t.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("480794.89"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 15),
        descripcion="Compra general",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_compra)

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        tarjeta_id=t.id,
        transaccion_padre_id=tx_compra.id,
        descripcion="Compra general",
        cantidad_cuotas=1,
        monto_total=Decimal("480794.89"),
        total_financiado=Decimal("480794.89"),
        moneda=Moneda.ARS,
    )
    db.add(grupo)
    tx_compra.grupo_cuotas_id = grupo.id

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_compra.id,
        numero_cuota=1,
        monto_proyectado=Decimal("480794.89"),
        monto_real=Decimal("480794.89"),
        fecha_vencimiento=vto_resumen,
        pagada=False,
    )
    db.add(cuota)

    db.commit()
    return u, b, t, cuota, cat_banco, subcat_impuestos, cat_super, subcat_alim, vto_resumen


def test_caso_1_pago_con_cargos_banco(db: Session):
    """
    1. Pago de $489.794,89 con 'cargos_banco' -> 2 transacciones nuevas:
       el pago por $480.794,89 con pago_resumen_vencimiento, y un egreso de $9.000,00
       con pago_origen_id = id del pago, categoría Banco, subcategoría Impuestos
       y descripción 'Cargos del resumen 5077'.
       Saldo de la billetera: $510.205,11.
       Las cuotas del resumen quedan pagadas igual que pagando $480.794,89.
       monto_diferencia == 9000.00.
    """
    u, b, t, cuota, cat_banco, subcat_imp, _, _, vto = _crear_escenario_base(db)

    tx_pago = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=u.id,
        tarjeta_id=t.id,
        fecha_pago=vto,
        fecha_resumen=vto,
        monto=Decimal("489794.89"),
        moneda=Moneda.ARS,
        diferencia_tipo="cargos_banco",
        commit=True,
    )

    # 1. Pago por el total calculado
    assert tx_pago.monto == Decimal("480794.89")
    assert tx_pago.pago_resumen_vencimiento == vto
    assert getattr(tx_pago, "monto_diferencia") == Decimal("9000.00")

    # 2. Transacción de egreso de diferencia vinculada
    tx_excedente = db.query(Transaccion).filter(Transaccion.pago_origen_id == tx_pago.id).first()
    assert tx_excedente is not None
    assert tx_excedente.monto == Decimal("9000.00")
    assert tx_excedente.tipo == TipoTransaccion.EGRESO
    assert tx_excedente.pago_resumen_vencimiento is None
    assert tx_excedente.categoria_id == cat_banco.id
    assert tx_excedente.subcategoria_id == subcat_imp.id
    assert tx_excedente.descripcion == "Cargos del resumen 5077"

    # 3. Saldo de la billetera: 1.000.000 - 480.794,89 - 9.000,00 = 510.205,11
    db.refresh(b)
    assert b.saldo_actual == Decimal("510205.11")

    # 4. Cuota pagada
    db.refresh(cuota)
    assert cuota.pagada is True
    assert cuota.transaccion_pago_id == tx_pago.id


def test_caso_2_pago_con_compras_no_cargadas(db: Session):
    """
    2. Mismo pago con 'compras_no_cargadas' y una categoría y subcategoría de egreso
       del catálogo de prueba -> el egreso de $9.000,00 tiene esa categoría y subcategoría
       y la descripción 'Compras no cargadas - Resumen 5077'.
    """
    u, b, t, _, _, _, cat_super, subcat_alim, vto = _crear_escenario_base(db)

    tx_pago = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=u.id,
        tarjeta_id=t.id,
        fecha_pago=vto,
        fecha_resumen=vto,
        monto=Decimal("489794.89"),
        moneda=Moneda.ARS,
        diferencia_tipo="compras_no_cargadas",
        diferencia_categoria_id=cat_super.id,
        diferencia_subcategoria_id=subcat_alim.id,
        commit=True,
    )

    assert tx_pago.monto == Decimal("480794.89")
    tx_excedente = db.query(Transaccion).filter(Transaccion.pago_origen_id == tx_pago.id).first()
    assert tx_excedente is not None
    assert tx_excedente.monto == Decimal("9000.00")
    assert tx_excedente.categoria_id == cat_super.id
    assert tx_excedente.subcategoria_id == subcat_alim.id
    assert tx_excedente.descripcion == "Compras no cargadas - Resumen 5077"


def test_caso_3_compras_no_cargadas_sin_categoria(db: Session):
    """
    3. 'compras_no_cargadas' sin categoría -> 400 con el texto exacto
       'Elegí la categoría de las compras que no cargaste.' y sin transacciones nuevas.
    """
    u, b, t, cuota, _, _, _, _, vto = _crear_escenario_base(db)
    tx_count_antes = db.query(Transaccion).count()

    with pytest.raises(HTTPException) as exc_info:
        pago_resumen_service.pagar_resumen_tarjeta(
            db=db,
            usuario_id=u.id,
            tarjeta_id=t.id,
            fecha_pago=vto,
            fecha_resumen=vto,
            monto=Decimal("489794.89"),
            moneda=Moneda.ARS,
            diferencia_tipo="compras_no_cargadas",
            diferencia_categoria_id=None,
            commit=True,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Elegí la categoría de las compras que no cargaste."

    # Sin transacciones nuevas y cuota impaga
    assert db.query(Transaccion).count() == tx_count_antes
    db.refresh(cuota)
    assert cuota.pagada is False
    db.refresh(b)
    assert b.saldo_actual == Decimal("1000000.00")


def test_caso_4_borrar_pago_del_caso_1(db: Session):
    """
    4. Borrar el pago del caso 1 -> se borra también el egreso de $9.000,00
       y el saldo vuelve a $1.000.000,00.
    """
    u, b, t, cuota, _, _, _, _, vto = _crear_escenario_base(db)

    tx_pago = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=u.id,
        tarjeta_id=t.id,
        fecha_pago=vto,
        fecha_resumen=vto,
        monto=Decimal("489794.89"),
        moneda=Moneda.ARS,
        diferencia_tipo="cargos_banco",
        commit=True,
    )

    db.refresh(b)
    assert b.saldo_actual == Decimal("510205.11")

    # Eliminar el pago
    transaccion_service.eliminar_transaccion(db, u.id, tx_pago.id, commit=True)

    # El egreso de 9.000,00 desaparece
    tx_excedente = db.query(Transaccion).filter(Transaccion.pago_origen_id == tx_pago.id).first()
    assert tx_excedente is None

    # El saldo de la billetera vuelve a $1.000.000,00
    db.refresh(b)
    assert b.saldo_actual == Decimal("1000000.00")

    # La cuota vuelve a impaga
    db.refresh(cuota)
    assert cuota.pagada is False


def test_caso_5_pesificacion_monto_mayor_a_total(db: Session):
    """
    5. Con pesificación y monto mayor al total -> el mismo 400 de hoy.
    """
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)

    b_ars = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        es_inversion=False,
    )
    db.add(b_ars)

    t_usd = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        nombre="Visa USD 1234",
        red=RedTarjeta.VISA,
        moneda=Moneda.USD,
        dia_cierre=20,
        dia_vencimiento=5,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(t_usd)

    vto = date(2026, 10, 5)
    tx_usd = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        tarjeta_id=t_usd.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 9, 15),
        descripcion="Consumo USD",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_usd)

    grupo_usd = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        tarjeta_id=t_usd.id,
        transaccion_padre_id=tx_usd.id,
        descripcion="Consumo USD",
        cantidad_cuotas=1,
        monto_total=Decimal("100.00"),
        total_financiado=Decimal("100.00"),
        moneda=Moneda.USD,
    )
    db.add(grupo_usd)
    tx_usd.grupo_cuotas_id = grupo_usd.id

    cuota_usd = Cuota(
        id=uuid4(),
        grupo_id=grupo_usd.id,
        transaccion_id=tx_usd.id,
        numero_cuota=1,
        monto_proyectado=Decimal("100.00"),
        monto_real=Decimal("100.00"),
        fecha_vencimiento=vto,
        pagada=False,
    )
    db.add(cuota_usd)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        pago_resumen_service.pagar_resumen_tarjeta(
            db=db,
            usuario_id=u.id,
            tarjeta_id=t_usd.id,
            fecha_pago=vto,
            fecha_resumen=vto,
            monto=Decimal("150.00"),
            moneda=Moneda.USD,
            pesificar=True,
            billetera_id=b_ars.id,
            cotizacion_personalizada=Decimal("1400.00"),
            commit=True,
        )

    assert exc_info.value.status_code == 400
    assert "no puede superar el total a pagar del resumen" in exc_info.value.detail
    assert "US$150" in exc_info.value.detail
    assert "US$100" in exc_info.value.detail


def test_caso_6_pago_menor_al_total(db: Session):
    """
    6. Pago de $300.000,00 -> igual que hoy (sin egreso extra; monto_diferencia None).
    """
    u, b, t, _, _, _, _, _, vto = _crear_escenario_base(db)

    tx_pago = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=u.id,
        tarjeta_id=t.id,
        fecha_pago=vto,
        fecha_resumen=vto,
        monto=Decimal("300000.00"),
        moneda=Moneda.ARS,
        commit=True,
    )

    assert tx_pago.monto == Decimal("300000.00")
    assert getattr(tx_pago, "monto_diferencia") is None

    # No hay transacciones con pago_origen_id vinculadas
    tx_vinculadas = db.query(Transaccion).filter(Transaccion.pago_origen_id == tx_pago.id).all()
    assert len(tx_vinculadas) == 0

    db.refresh(b)
    assert b.saldo_actual == Decimal("700000.00")


def test_caso_7_definiciones_gasto_del_ciclo(db: Session):
    """
    7. El gasto del ciclo según definiciones_service incluye los $9.000,00 del caso 1
       y no incluye el pago de $480.794,89.
    """
    u, b, t, _, _, _, _, _, vto = _crear_escenario_base(db)

    tx_pago = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=u.id,
        tarjeta_id=t.id,
        fecha_pago=vto,
        fecha_resumen=vto,
        monto=Decimal("489794.89"),
        moneda=Moneda.ARS,
        diferencia_tipo="cargos_banco",
        commit=True,
    )

    # Filtrar transacciones consideradas gasto según definiciones_service
    gastos = db.query(Transaccion).filter(
        definiciones_service.condicion_gasto(
            usuario_id=u.id,
            desde=date(2026, 10, 1),
            hasta=date(2026, 10, 31),
            hoy=date(2026, 10, 9),
        )
    ).all()

    gastos_ids = [g.id for g in gastos]

    # El pago de resumen ($480.794,89) NO es gasto
    assert tx_pago.id not in gastos_ids

    # El egreso de cargos bancarios ($9.000,00) SÍ es gasto
    tx_excedente = db.query(Transaccion).filter(Transaccion.pago_origen_id == tx_pago.id).first()
    assert tx_excedente is not None
    assert tx_excedente.id in gastos_ids
    assert tx_excedente.monto == Decimal("9000.00")
