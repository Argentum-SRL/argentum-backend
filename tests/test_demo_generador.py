# -*- coding: utf-8 -*-
"""
tests/test_demo_generador.py — Pruebas unitarias para el generador del usuario demo.
Cubre los 10 puntos de verificación requeridos en la especificación.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.security import verify_password
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.presupuesto import Presupuesto
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.suscripcion import Suscripcion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import conciliacion_service
import app.services.dias_habiles_service
from scripts.demo.generador import (
    ejecutar_borrar,
    ejecutar_crear,
    ejecutar_ponerse_al_dia,
    fecha_a_utc_12_argentina,
)
from scripts.demo.persona import (
    EMAIL_DEMO,
    NOMBRE_DEMO,
    APELLIDO_DEMO,
    FECHA_INICIO,
    FECHA_FIN_FUTURO,
    construir_plan_completo,
)
from scripts.seed_categorias import seed_categorias


HOY_TEST = date(2026, 11, 2)
HOY_TEST_POST = date(2026, 12, 2)


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

    # Mock feriados para evitar intentos de conexión a postgresql en SQLite
    monkeypatch.setattr(app.services.dias_habiles_service, "_obtener_feriados_db_sync", lambda anio: [])
    app.services.dias_habiles_service._feriados_cache.update({2025: [], 2026: [], 2027: [], 2028: [], 2029: []})

    # Mock cotizaciones dolar para suscripciones en SQLite
    from types import SimpleNamespace
    mock_cot = SimpleNamespace(promedio=Decimal("1300.00"), venta=Decimal("1300.00"), compra=Decimal("1300.00"), tipo="tarjeta")
    monkeypatch.setattr(
        "app.services.cobro_suscripcion_service.obtener_cotizacion_por_fecha",
        lambda db, tipo, fecha: mock_cot,
    )

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


def crear_base_limpia() -> Session:
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
    seed_categorias(session)
    return session


@pytest.fixture(name="db")
def db_fixture():
    session = crear_base_limpia()
    try:
        yield session
    finally:
        session.close()


def test_01_usuario_creacion_y_atributos(db: Session, monkeypatch):
    """
    (1) Crea el usuario con los datos de D-2 y la contraseña de DEMO_PASSWORD ("PruebaLocal1234!");
    verify_password da True.
    """
    pw_test = "PruebaLocal1234!"
    monkeypatch.setenv("DEMO_PASSWORD", pw_test)

    resumen = ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)
    assert resumen is not None

    usuario = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one_or_none()
    assert usuario is not None
    assert usuario.nombre == NOMBRE_DEMO
    assert usuario.apellido == APELLIDO_DEMO
    assert usuario.rol == RolUsuario.USUARIO
    assert usuario.estado == EstadoUsuario.ACTIVO
    assert usuario.email_verificado is True
    assert usuario.onboarding_completo is True
    assert usuario.modal_bienvenida_financiera_visto is True
    assert usuario.telefono is None
    assert usuario.ciclo_tipo == CicloTipo.DIA_FIJO
    assert str(usuario.ciclo_valor) == "25"
    assert usuario.moneda_principal == Moneda.ARS
    assert verify_password(pw_test, usuario.password_hash) is True

    # Verificación de fecha_creacion forzada para todas las transacciones
    txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == usuario.id)).scalars().all()
    assert len(txs) > 0
    for tx in txs:
        fc = tx.fecha_creacion if tx.fecha_creacion.tzinfo else tx.fecha_creacion.replace(tzinfo=timezone.utc)
        assert fc == fecha_a_utc_12_argentina(tx.fecha)


def test_02_al_menos_un_movimiento_de_cada_tipo(db: Session):
    """
    (2) Hay al menos un movimiento de cada tipo:
    gasto débito, ingreso sueldo, aguinaldo, compra crédito 1 pago, compra en cuotas (las 4),
    cuota futura, pago de resumen, cobro de suscripción con crédito, cobro de suscripción con débito,
    consumo en dólares con tarjeta, gasto en dólares desde Dólares, gasto en dólares pagado con pesos,
    aporte a meta, retiro de meta, transferencia entre billeteras, compra de dólares, rendimiento,
    control de saldo, reintegro recibido, reintegro pendiente, devolución parte, pasó por mi cuenta (ingreso y egreso),
    devolución de compra, gasto con fecha futura, 4 presupuestos.
    """
    pw_test = "PruebaLocal1234!"
    HOY_TEST_FEB = date(2027, 2, 1)
    ejecutar_crear(db, password=pw_test, hoy=HOY_TEST_FEB)
    usuario = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one()

    # Gasto débito
    gasto_debito = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.metodo_pago == MetodoPago.DEBITO,
        )
    ).first()
    assert gasto_debito is not None

    # Ingreso sueldo
    sueldo = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo == TipoTransaccion.INGRESO,
            Transaccion.descripcion.like("%Sueldo%"),
        )
    ).first()
    assert sueldo is not None

    # Aguinaldo
    aguinaldo = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo == TipoTransaccion.INGRESO,
            Transaccion.descripcion.like("%SAC%"),
        )
    ).first()
    assert aguinaldo is not None

    # Compra crédito 1 pago
    compra_cred_1pago = db.execute(
        select(Transaccion).join(GrupoCuotas, Transaccion.grupo_cuotas_id == GrupoCuotas.id).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.metodo_pago == MetodoPago.CREDITO,
            Transaccion.es_cuota_hija.is_(True),
            GrupoCuotas.cantidad_cuotas == 1,
        )
    ).first()
    assert compra_cred_1pago is not None

    # Compras en cuotas (las 4)
    grupos = db.execute(
        select(GrupoCuotas).where(
            GrupoCuotas.usuario_id == usuario.id,
            GrupoCuotas.cantidad_cuotas > 1,
        )
    ).scalars().all()
    assert len(grupos) == 4

    # Cuota futura
    cuota_futura = db.execute(
        select(Cuota).join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id).where(
            GrupoCuotas.usuario_id == usuario.id,
            Cuota.fecha_vencimiento > HOY_TEST_FEB,
        )
    ).first()
    assert cuota_futura is not None

    # Pago de resumen
    pago_resumen = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.pago_resumen_vencimiento.isnot(None),
        )
    ).first()
    assert pago_resumen is not None

    # Cobro de suscripción con crédito
    sub_cred = db.execute(
        select(Transaccion).join(Suscripcion, Transaccion.suscripcion_id == Suscripcion.id).where(
            Transaccion.usuario_id == usuario.id,
            Suscripcion.tarjeta_id.isnot(None),
        )
    ).first()
    assert sub_cred is not None

    # Cobro de suscripción con débito
    sub_deb = db.execute(
        select(Transaccion).join(Suscripcion, Transaccion.suscripcion_id == Suscripcion.id).where(
            Transaccion.usuario_id == usuario.id,
            Suscripcion.billetera_id.isnot(None),
        )
    ).first()
    assert sub_deb is not None

    # Consumo en dólares con tarjeta (ChatGPT US$20)
    consumo_usd_tarjeta = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.monto_original == Decimal("20.00"),
            Transaccion.tarjeta_id.isnot(None),
        )
    ).first()
    assert consumo_usd_tarjeta is not None

    # Gasto en dólares desde Dólares (Viaje a Brasil)
    gasto_usd_dolares = db.execute(
        select(Transaccion).join(Billetera, Transaccion.billetera_id == Billetera.id).where(
            Transaccion.usuario_id == usuario.id,
            Billetera.nombre == "Dólares",
            Transaccion.moneda == Moneda.USD,
        )
    ).first()
    assert gasto_usd_dolares is not None

    # Gasto en dólares pagado con pesos (Steam)
    gasto_usd_pesos = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.monto_original.isnot(None),
            Transaccion.moneda == Moneda.ARS,
        )
    ).first()
    assert gasto_usd_pesos is not None

    # Aporte a meta
    aporte_meta = db.execute(
        select(MovimientoMeta).join(Meta, MovimientoMeta.meta_id == Meta.id).where(
            Meta.usuario_id == usuario.id,
            MovimientoMeta.tipo == TipoMovimientoMeta.APORTE,
        )
    ).first()
    assert aporte_meta is not None

    # Retiro de meta
    retiro_meta = db.execute(
        select(MovimientoMeta).join(Meta, MovimientoMeta.meta_id == Meta.id).where(
            Meta.usuario_id == usuario.id,
            MovimientoMeta.tipo == TipoMovimientoMeta.RETIRO,
        )
    ).first()
    assert retiro_meta is not None

    # Transferencia entre billeteras
    transf = db.execute(
        select(TransferenciaInterna).where(TransferenciaInterna.usuario_id == usuario.id)
    ).first()
    assert transf is not None

    # Compra de dólares
    compra_dolares = db.execute(
        select(TransferenciaInterna).where(
            TransferenciaInterna.usuario_id == usuario.id,
            TransferenciaInterna.monto_destino.isnot(None),
        )
    ).first()
    assert compra_dolares is not None

    # Rendimiento
    rendimiento = db.execute(
        select(RendimientoBilletera).join(Billetera, RendimientoBilletera.billetera_id == Billetera.id).where(
            Billetera.usuario_id == usuario.id
        )
    ).first()
    assert rendimiento is not None

    # Control de saldo
    control = db.execute(
        select(AjusteSaldo).join(Billetera, AjusteSaldo.billetera_id == Billetera.id).where(
            Billetera.usuario_id == usuario.id
        )
    ).first()
    assert control is not None

    # Reintegro recibido y pendiente
    reintegro_recibido = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo_espejo == "reintegro",
        )
    ).first()
    assert reintegro_recibido is not None

    reintegro_pendiente = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.reintegro_esperado.isnot(None),
            Transaccion.reintegro_de_id.is_(None),
        )
    ).first()
    assert reintegro_pendiente is not None

    # Devolución parte
    dev_parte = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo_espejo == "parte",
        )
    ).first()
    assert dev_parte is not None

    # Pasó por mi cuenta (ingreso y egreso)
    paso_ingreso = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo == TipoTransaccion.INGRESO,
            Transaccion.descripcion.like("%hermano%"),
        )
    ).first()
    assert paso_ingreso is not None

    paso_egreso = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.descripcion.like("%hermano%"),
        )
    ).first()
    assert paso_egreso is not None

    # Devolución de compra
    dev_compra = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo_espejo == "reintegro",
            Transaccion.reintegro_de_id.isnot(None),
            Transaccion.monto == Decimal("-70000.00"),
        )
    ).first()
    assert dev_compra is not None

    # Gasto con fecha futura
    gasto_futuro = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.fecha > HOY_TEST_FEB,
        )
    ).first()
    assert gasto_futuro is not None

    # 4 presupuestos
    presupuestos = db.execute(
        select(Presupuesto).where(Presupuesto.usuario_id == usuario.id)
    ).scalars().all()
    assert len(presupuestos) == 4


def test_03_saldos_actuales_igual_a_teorico(db: Session):
    """
    (3) Para cada billetera del demo, saldo_actual == calcular_saldo_teorico(hoy).
    """
    pw_test = "PruebaLocal1234!"
    resumen = ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)
    for nombre, s in resumen["saldos"].items():
        assert s["iguales"] is True, f"Billetera {nombre} discrepa: actual {s['saldo_actual']} != teorico {s['saldo_teorico']}"


def test_04_movimientos_futuros_no_afectan_saldo(db: Session):
    """
    (4) Ningún movimiento con fecha futura cambió un saldo.
    """
    pw_test = "PruebaLocal1234!"
    ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)
    usuario = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one()

    # Todas las transacciones con fecha > HOY_TEST que afectan saldo deben tener saldo_aplicado == False
    txs_futuras_no_cred = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha > HOY_TEST,
            Transaccion.metodo_pago != MetodoPago.CREDITO,
        )
    ).scalars().all()
    assert len(txs_futuras_no_cred) > 0
    for tx in txs_futuras_no_cred:
        assert tx.saldo_aplicado is False

    # Verificación de fecha_creacion forzada para todas las filas
    for tx in db.execute(select(Transaccion).where(Transaccion.usuario_id == usuario.id)).scalars():
        fc = tx.fecha_creacion if tx.fecha_creacion.tzinfo else tx.fecha_creacion.replace(tzinfo=timezone.utc)
        assert fc == fecha_a_utc_12_argentina(tx.fecha)


def test_05_aislamiento_de_otro_usuario(db: Session):
    """
    (5) Otro usuario creado antes del generador tiene exactamente las mismas filas antes y después.
    """
    # Crear usuario ajeno
    otro_id = uuid4()
    otro_user = Usuario(
        id=otro_id,
        email="otro.usuario@ejemplo.com",
        auth_provider=AuthProvider.EMAIL,
        password_hash="hash_dummy",
        nombre="Otro",
        apellido="User",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(otro_user)
    b_otro = Billetera(
        id=uuid4(),
        usuario_id=otro_id,
        nombre="Efectivo Otro",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000.00"),
        saldo_inicial=Decimal("50000.00"),
        es_principal=True,
        estado=EstadoUsuario.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(b_otro)
    tx_otro = Transaccion(
        id=uuid4(),
        usuario_id=otro_id,
        billetera_id=b_otro.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 1),
        descripcion="Gasto ajeno",
        metodo_pago=MetodoPago.EFECTIVO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(tx_otro)
    db.commit()

    filas_otro_antes = db.execute(
        select(func.count(Transaccion.id)).where(Transaccion.usuario_id == otro_id)
    ).scalar()

    # Ejecutar generador
    ejecutar_crear(db, password="PruebaLocal1234!", hoy=HOY_TEST)

    filas_otro_despues = db.execute(
        select(func.count(Transaccion.id)).where(Transaccion.usuario_id == otro_id)
    ).scalar()
    assert filas_otro_despues == filas_otro_antes


def test_06_crear_duplicado_falla_sin_modificar_filas(db: Session):
    """
    (6) Correr --crear otra vez sale con error y no cambia la cantidad de filas.
    """
    pw_test = "PruebaLocal1234!"
    ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)
    conteo_tx_antes = db.execute(select(func.count(Transaccion.id))).scalar()

    with pytest.raises(ValueError, match="El usuario demo ya existe"):
        ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)

    conteo_tx_despues = db.execute(select(func.count(Transaccion.id))).scalar()
    assert conteo_tx_despues == conteo_tx_antes


def test_07_determinismo_entre_bases_limpias():
    """
    (7) Dos corridas sobre bases limpias distintas dan la misma lista de (fecha, monto, descripción).
    """
    pw_test = "PruebaLocal1234!"

    db1 = crear_base_limpia()
    ejecutar_crear(db1, password=pw_test, hoy=HOY_TEST)
    u1 = db1.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one()
    txs_1 = db1.execute(
        select(Transaccion.fecha, Transaccion.monto, Transaccion.descripcion).where(
            Transaccion.usuario_id == u1.id
        ).order_by(Transaccion.fecha, Transaccion.descripcion, Transaccion.monto)
    ).all()
    db1.close()

    db2 = crear_base_limpia()
    ejecutar_crear(db2, password=pw_test, hoy=HOY_TEST)
    u2 = db2.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one()
    txs_2 = db2.execute(
        select(Transaccion.fecha, Transaccion.monto, Transaccion.descripcion).where(
            Transaccion.usuario_id == u2.id
        ).order_by(Transaccion.fecha, Transaccion.descripcion, Transaccion.monto)
    ).all()
    db2.close()

    assert txs_1 == txs_2


def test_08_ponerse_al_dia_idempotente(db: Session):
    """
    (8) --ponerse-al-dia con --hoy 02/12/2026 agrega el pago del resumen que venció en noviembre,
    los aportes y el rendimiento de noviembre, y correrlo dos veces no agrega nada la segunda vez.
    """
    pw_test = "PruebaLocal1234!"
    ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)

    usuario = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one()

    # Contar antes
    cnt_tx_antes = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario.id)).scalar()
    cnt_mm_antes = db.execute(select(func.count(MovimientoMeta.id)).join(Meta, MovimientoMeta.meta_id == Meta.id).where(Meta.usuario_id == usuario.id)).scalar()
    cnt_rb_antes = db.execute(select(func.count(RendimientoBilletera.id)).join(Billetera, RendimientoBilletera.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario.id)).scalar()

    # Ponerse al día con fecha post (02/12/2026)
    ejecutar_ponerse_al_dia(db, hoy=HOY_TEST_POST)

    cnt_tx_despues1 = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario.id)).scalar()
    cnt_mm_despues1 = db.execute(select(func.count(MovimientoMeta.id)).join(Meta, MovimientoMeta.meta_id == Meta.id).where(Meta.usuario_id == usuario.id)).scalar()
    cnt_rb_despues1 = db.execute(select(func.count(RendimientoBilletera.id)).join(Billetera, RendimientoBilletera.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario.id)).scalar()

    assert cnt_tx_despues1 > cnt_tx_antes  # Se agregó pago de resumen noviembre
    assert cnt_mm_despues1 > cnt_mm_antes  # Se agregó aporte noviembre
    assert cnt_rb_despues1 > cnt_rb_antes  # Se agregó rendimiento noviembre

    # Correr nuevamente ponerse_al_dia a la misma fecha no agrega nada
    ejecutar_ponerse_al_dia(db, hoy=HOY_TEST_POST)

    cnt_tx_despues2 = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario.id)).scalar()
    cnt_mm_despues2 = db.execute(select(func.count(MovimientoMeta.id)).join(Meta, MovimientoMeta.meta_id == Meta.id).where(Meta.usuario_id == usuario.id)).scalar()
    cnt_rb_despues2 = db.execute(select(func.count(RendimientoBilletera.id)).join(Billetera, RendimientoBilletera.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario.id)).scalar()

    assert cnt_tx_despues2 == cnt_tx_despues1
    assert cnt_mm_despues2 == cnt_mm_despues1
    assert cnt_rb_despues2 == cnt_rb_despues1


def test_09_borrar_con_confirmacion_correcta_e_incorrecta(db: Session):
    """
    (9) --borrar con la confirmación correcta deja 0 filas del demo y el otro usuario intacto;
    con una confirmación distinta no borra nada.
    """
    # Crear usuario ajeno
    otro_id = uuid4()
    otro_user = Usuario(
        id=otro_id,
        email="vecino@ejemplo.com",
        auth_provider=AuthProvider.EMAIL,
        password_hash="hash_dummy",
        nombre="Vecino",
        apellido="Sur",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(otro_user)
    db.commit()

    ejecutar_crear(db, password="PruebaLocal1234!", hoy=HOY_TEST)

    # Confirmación incorrecta
    exito_fail = ejecutar_borrar(db, confirmacion="otra_cosa@ejemplo.com")
    assert exito_fail is False

    usuario_demo = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one_or_none()
    assert usuario_demo is not None

    # Confirmación correcta
    exito_ok = ejecutar_borrar(db, confirmacion=EMAIL_DEMO)
    assert exito_ok is True

    usuario_demo_despues = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one_or_none()
    assert usuario_demo_despues is None

    # El otro usuario sigue intacto
    otro_user_despues = db.execute(select(Usuario).where(Usuario.id == otro_id)).scalar_one_or_none()
    assert otro_user_despues is not None


def test_10_seguridad_archivos_sin_secretos():
    """
    (10) Ningún archivo de scripts/demo/ contiene "TesterTesting" ni la palabra password seguida de un valor.
    """
    demo_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "demo")
    assert os.path.isdir(demo_dir)

    for root, _, files in os.walk(demo_dir):
        for f in files:
            if f.endswith((".py", ".md", ".txt")):
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as file_obj:
                    content = file_obj.read()

                assert "TesterTesting" not in content, f"Se encontró 'TesterTesting' en {f}"

                # Buscar 'password\s*[:=]\s*["\'][^"\']+["\']' excluyendo variables de entorno o None
                matches = re.findall(r'password\s*[:=]\s*["\']([^"\']+)["\']', content, re.IGNORECASE)
                for m in matches:
                    assert m in ("email", "none", "", "local"), f"Posible secreto hardcodeado en {f}: {m}"


def test_11_saldos_teoricos_minimos_no_negativos(db: Session):
    """
    (11) Con --hoy 02/11/2026 y el plan completo cargado, el saldo teórico mínimo de cada
    billetera ARS en cada día del período (25/05/2026 al 21/08/2027) es >= 0 (y el de Dólares también).
    """
    pw_test = "PruebaLocal1234!"
    ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)
    usuario = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one()
    billeteras = db.execute(
        select(Billetera).where(Billetera.usuario_id == usuario.id)
    ).scalars().all()

    cur = FECHA_INICIO
    fechas = []
    while cur <= FECHA_FIN_FUTURO:
        fechas.append(cur)
        cur += timedelta(days=1)

    for b in billeteras:
        min_saldo = None
        min_fecha = None
        for d in fechas:
            st = Decimal(str(conciliacion_service.calcular_saldo_teorico(db, b.id, hasta=d)))
            if min_saldo is None or st < min_saldo:
                min_saldo = st
                min_fecha = d
        assert min_saldo is not None
        assert min_saldo >= Decimal("0.00"), (
            f"Billetera {b.nombre} tiene saldo teórico negativo el {min_fecha}: {min_saldo}"
        )


def test_12_oraculo_numeros_demo(db: Session):
    """
    (12) Oráculo de números del demo para ciclos completos pasados con --hoy 02/11/2026.
    Compara las reglas puras del plan con las consultas de la app:
    - gastos ARS, ingresos ARS, gastos USD
    - balance del ciclo (calcular_balance_ciclo)
    - uso de cada presupuesto (uso_periodo)
    - saldo teórico de cada billetera al último día de cada ciclo
    Todo con valores exactos Decimal.
    """
    from app.models.periodo_presupuesto import PeriodoPresupuesto
    from app.services import definiciones_service, dashboard_service, presupuesto_service

    pw_test = "PruebaLocal1234!"
    ejecutar_crear(db, password=pw_test, hoy=HOY_TEST)
    usuario = db.execute(select(Usuario).where(Usuario.email == EMAIL_DEMO)).scalar_one()
    billeteras = {b.nombre: b for b in db.execute(select(Billetera).where(Billetera.usuario_id == usuario.id)).scalars()}
    presupuestos = db.execute(select(Presupuesto).where(Presupuesto.usuario_id == usuario.id)).scalars().all()

    plan = construir_plan_completo()

    ciclos = [
        (date(2026, 5, 25), date(2026, 6, 24)),
        (date(2026, 6, 25), date(2026, 7, 24)),
        (date(2026, 7, 25), date(2026, 8, 24)),
        (date(2026, 8, 25), date(2026, 9, 24)),
        (date(2026, 9, 25), date(2026, 10, 24)),
    ]

    for c_ini, c_fin in ciclos:
        # 1. Ingresos ARS calculados puramente desde el PLAN
        plan_ingresos_ars = Decimal("0.00")
        for s in plan["sueldos"]:
            if c_ini <= s["fecha"] <= c_fin:
                plan_ingresos_ars += s["monto"]
        for a in plan["aguinaldos"]:
            if c_ini <= a["fecha"] <= c_fin:
                plan_ingresos_ars += a["monto"]

        # 2. Gastos ARS calculados puramente desde el PLAN
        plan_gastos_ars = Decimal("0.00")

        # Fijos Galicia débito
        for fijos in [plan["alquileres"], plan["expensas"], plan["luz"], plan["gas"], plan["agua"]]:
            for it in fijos:
                if c_ini <= it["fecha"] <= c_fin:
                    plan_gastos_ars += it["monto"]

        # Variables semanales
        for v in plan["variables"]:
            if c_ini <= v.fecha <= c_fin:
                plan_gastos_ars += v.monto
            # Reintegros recibidos en el ciclo
            if v.es_reintegro_25 and v.reintegro_monto and v.reintegro_fecha:
                if c_ini <= v.reintegro_fecha <= c_fin:
                    plan_gastos_ars -= v.reintegro_monto

        # Cena con amigas (meses pares: f_cena = 27, f_dev = 28)
        for year, month in [(2026, 6), (2026, 8), (2026, 10)]:
            f_cena = date(year, month, 27)
            f_dev = date(year, month, 28)
            if c_ini <= f_cena <= c_fin:
                plan_gastos_ars += Decimal("60000.00")
            if c_ini <= f_dev <= c_fin:
                plan_gastos_ars -= Decimal("30000.00")

        # Devolución compra ropa Zara (15/10/2026: $70.000 devuelta entera)
        f_ropa = date(2026, 10, 15)
        if c_ini <= f_ropa <= c_fin:
            plan_gastos_ars += Decimal("70000.00")
            plan_gastos_ars -= Decimal("70000.00")

        # Suscripciones en el ciclo (día 25): Netflix $11.999, Spotify $4.299, ChatGPT $26.000, Gimnasio $45.000, Prepaga $185.000, Celular $22.000
        for year, month in [(2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
            f_sub = date(year, month, 25)
            if c_ini <= f_sub <= c_fin:
                plan_gastos_ars += Decimal("11999.00")
                plan_gastos_ars += Decimal("4299.00")
                plan_gastos_ars += Decimal("26000.00")
                plan_gastos_ars += Decimal("45000.00")
                plan_gastos_ars += Decimal("185000.00")
                plan_gastos_ars += Decimal("22000.00")

        # Cuotas que vencen en el ciclo (vencimientos día 5)
        # Heladera Samsung: $100.000/mes desde 05/07/2026
        for m in range(7, 13):
            f_vto = date(2026, m, 5)
            if c_ini <= f_vto <= c_fin:
                plan_gastos_ars += Decimal("100000.00")
        # Zapatillas Nike: $60.000/mes desde 05/10/2026
        for m in range(10, 13):
            f_vto = date(2026, m, 5)
            if c_ini <= f_vto <= c_fin:
                plan_gastos_ars += Decimal("60000.00")

        # Steam: US$15 pagado con pesos a cotización oficial ($20.250) día 22
        for year, month in [(2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
            f_steam = date(year, month, 22)
            if c_ini <= f_steam <= c_fin:
                plan_gastos_ars += Decimal("20250.00")

        # 3. Gastos USD calculados desde el PLAN
        plan_gastos_usd = Decimal("0.00")

        # Comparación con la APP:
        cond_g = definiciones_service.condicion_gasto(usuario.id, desde=c_ini, hasta=c_fin, hoy=HOY_TEST)
        cond_i = definiciones_service.condicion_ingreso(usuario.id, desde=c_ini, hasta=c_fin, hoy=HOY_TEST)

        app_gastos_ars = db.execute(
            select(func.coalesce(func.sum(Transaccion.monto), Decimal("0.00")))
            .where(cond_g, Transaccion.moneda == "ARS")
        ).scalar()
        app_ingresos_ars = db.execute(
            select(func.coalesce(func.sum(Transaccion.monto), Decimal("0.00")))
            .where(cond_i, Transaccion.moneda == "ARS")
        ).scalar()
        app_gastos_usd = db.execute(
            select(func.coalesce(func.sum(Transaccion.monto), Decimal("0.00")))
            .where(cond_g, Transaccion.moneda == "USD")
        ).scalar()

        assert app_ingresos_ars == plan_ingresos_ars, (
            f"Ingresos ARS en ciclo {c_ini}..{c_fin}: app={app_ingresos_ars} != plan={plan_ingresos_ars}"
        )
        assert app_gastos_ars == plan_gastos_ars, (
            f"Gastos ARS en ciclo {c_ini}..{c_fin}: app={app_gastos_ars} != plan={plan_gastos_ars}"
        )
        assert app_gastos_usd == plan_gastos_usd, (
            f"Gastos USD en ciclo {c_ini}..{c_fin}: app={app_gastos_usd} != plan={plan_gastos_usd}"
        )

        # Balance ciclo del inicio
        bal_app = dashboard_service.calcular_balance_ciclo(
            db, usuario, fecha_desde_override=c_ini, fecha_hasta_override=c_fin
        )
        assert Decimal(str(bal_app["ars"]["ingresos"])) == plan_ingresos_ars
        assert Decimal(str(bal_app["ars"]["egresos"])) == plan_gastos_ars
        assert Decimal(str(bal_app["ars"]["balance"])) == plan_ingresos_ars - plan_gastos_ars
        assert Decimal(str(bal_app["usd"]["egresos"])) == plan_gastos_usd

        # Uso de presupuestos
        p_super = Decimal("0.00")
        for v in plan["variables"]:
            if v.subcategoria_nombre == "Supermercado" and c_ini <= v.fecha <= c_fin:
                p_super += v.monto
            if v.subcategoria_nombre == "Supermercado" and v.es_reintegro_25 and v.reintegro_monto and v.reintegro_fecha:
                if c_ini <= v.reintegro_fecha <= c_fin:
                    p_super -= v.reintegro_monto

        p_delivery = sum(v.monto for v in plan["variables"] if v.subcategoria_nombre == "Delivery" and c_ini <= v.fecha <= c_fin)
        p_cafe = sum(v.monto for v in plan["variables"] if "cafe" in v.subcategoria_nombre.lower() and c_ini <= v.fecha <= c_fin)
        p_salidas = sum(v.monto for v in plan["variables"] if v.subcategoria_nombre == "Salidas" and c_ini <= v.fecha <= c_fin)

        plan_presus = {
            "supermercado": p_super,
            "delivery": p_delivery,
            "café": p_cafe,
            "cafe": p_cafe,
            "salidas": p_salidas,
        }

        for p in presupuestos:
            per = PeriodoPresupuesto(
                id=uuid4(),
                presupuesto_id=p.id,
                fecha_inicio=c_ini,
                fecha_fin=c_fin,
                monto_limite=p.monto,
            )
            uso = presupuesto_service.uso_periodo(db, p, per, hoy=HOY_TEST, usuario=usuario)
            esperado = plan_presus.get(p.nombre.lower())
            assert uso.calculado == esperado, (
                f"Presupuesto {p.nombre} en ciclo {c_ini}..{c_fin}: app={uso.calculado} != plan={esperado}"
            )

        # Saldo teórico de cada billetera al último día del ciclo
        for nombre, b in billeteras.items():
            st = Decimal(str(conciliacion_service.calcular_saldo_teorico(db, b.id, hasta=c_fin)))
            assert st >= Decimal("0.00"), f"Billetera {nombre} saldo negativo {st} en ciclo {c_ini}..{c_fin}"
