"""
tests/test_reintegros_metodos.py — Pruebas de reintegros por medio de pago (fase5c_fix1).

Medios de pago (se repiten para débito, efectivo y transferencia):
- M1 Gasto de $100.000 en Supermercado con reintegro recibido de $25.000 el mismo día:
     saldo 425.000; el espejo es EGRESO de -25.000 CONFIRMADA en la misma billetera y categoría;
     gasto neto de la categoría (condicion_gasto) 75.000.
     Impacto en presupuesto: monto_usado 75.000.
- M2 Débito en USD: gasto USD 20 y reintegro USD 5 en billetera USD de 1.000:
     saldo USD 985, gasto neto USD 15.
- M3 Reintegro de $25.000 "todavía no" sobre un gasto de $100.000:
     saldo 400.000, gasto 100.000, sin espejo;
     al marcarlo recibido hoy: saldo 425.000, gasto neto 75.000.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import reintegro_service, transaccion_service
from app.services.definiciones_service import condicion_gasto
from app.services.presupuesto_service import calcular_gasto_en_periodo, uso_actual
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
        email="test_metodos@argentum.com",
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
def categoria_fixture(db: Session):
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
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
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
        nombre="Billetera USD",
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


@pytest.fixture(name="presupuesto_supermercado")
def presupuesto_supermercado_fixture(db: Session, usuario: Usuario, categoria_supermercado: Categoria):
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Presupuesto Supermercado",
        monto=Decimal("200000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(presu)
    db.flush()

    presu_cat = PresupuestoCategoria(
        presupuesto_id=presu.id,
        categoria_id=categoria_supermercado.id,
    )
    db.add(presu_cat)

    hoy = hoy_argentina()
    # Período del mes actual
    fecha_inicio = date(hoy.year, hoy.month, 1)
    if hoy.month == 12:
        fecha_fin = date(hoy.year + 1, 1, 1)
    else:
        fecha_fin = date(hoy.year, hoy.month + 1, 1)

    periodo = PeriodoPresupuesto(
        presupuesto_id=presu.id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        monto_limite=Decimal("200000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db.add(periodo)
    db.commit()
    db.refresh(presu)
    return presu


# ==============================================================================
# M1: Gasto de $100.000 con reintegro recibido de $25.000 el mismo día
# Repetido para Débito, Efectivo y Transferencia
# ==============================================================================
@pytest.mark.parametrize("metodo", [MetodoPago.DEBITO, MetodoPago.EFECTIVO, MetodoPago.TRANSFERENCIA])
def test_m1_gasto_y_reintegro_mismo_dia(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    metodo: MetodoPago,
):
    """M1: Gasto de $100.000 en Supermercado con reintegro recibido de $25.000 el mismo día:
    saldo 425.000; el espejo es EGRESO de -25.000 CONFIRMADA en la misma billetera y categoría;
    gasto neto de la categoría (condicion_gasto) 75.000."""
    hoy = hoy_argentina()

    # 1. Compra de $100.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra Supermercado M1",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=metodo,
        ),
    )

    # 2. Reintegro recibido de $25.000 el mismo día
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )

    # Validaciones M1
    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("425000.00"), "El saldo debe ser 425.000 (500k - 100k + 25k)"

    # Espejo
    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalars().all()
    assert len(espejos) == 1, "Debe existir exactamente un espejo"
    espejo = espejos[0]
    assert espejo.tipo == TipoTransaccion.EGRESO
    assert espejo.monto == Decimal("-25000.00")
    assert espejo.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert espejo.billetera_id == billetera_ars.id
    assert espejo.categoria_id == categoria_supermercado.id
    assert espejo.metodo_pago == metodo

    # Gasto neto de la categoría según condicion_gasto
    cond = condicion_gasto(usuario.id, desde=hoy, hasta=hoy, hoy=hoy)
    rows = db.execute(
        select(Transaccion.monto).where(
            cond,
            Transaccion.categoria_id == categoria_supermercado.id,
        )
    ).scalars().all()
    gasto_neto = sum(rows, Decimal("0.00"))
    assert gasto_neto == Decimal("75000.00"), "El gasto neto de la categoría debe ser 75.000 (100k - 25k)"


# ==============================================================================
# Presupuesto por medio de pago (débito, efectivo, transferencia)
# Debe fallar antes del arreglo y pasar después
# ==============================================================================
@pytest.mark.parametrize("metodo", [MetodoPago.DEBITO, MetodoPago.EFECTIVO, MetodoPago.TRANSFERENCIA])
def test_m1_presupuesto_por_medio_de_pago(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    presupuesto_supermercado: Presupuesto,
    metodo: MetodoPago,
):
    """Verifica que el reintegro de $25.000 descuenta del monto_usado del presupuesto activo
    para cada medio de pago (débito, efectivo, transferencia).
    Usado inicial tras compra 100k: 100.000. Tras reintegro 25k: 75.000."""
    hoy = hoy_argentina()

    # Compra de $100.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra Presupuesto Metodo",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=metodo,
        ),
    )
    assert uso_actual(db, presupuesto_supermercado, hoy=hoy)[1].mostrado == Decimal("100000.00"), "Tras compra de 100k, monto_usado debe ser 100.000"

    # Reintegro recibido de $25.000
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )
    assert uso_actual(db, presupuesto_supermercado, hoy=hoy)[1].mostrado == Decimal("75000.00"), "Tras reintegro de 25k, monto_usado debe ser 75.000"


# ==============================================================================
# M2: Débito en USD
# ==============================================================================
def test_m2_debito_en_usd(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_usd: Billetera,
):
    """M2 Débito en USD: gasto USD 20 y reintegro USD 5 en billetera USD de 1.000:
    saldo USD 985, gasto neto USD 15."""
    hoy = hoy_argentina()

    # Compra de USD 20
    compra_usd = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("20.00"),
            moneda=Moneda.USD,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra USD Supermercado",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_usd.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    # Reintegro de USD 5
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra_usd.id,
        monto=Decimal("5.00"),
        recibido=True,
        fecha_recibido=hoy,
    )

    # Validaciones M2
    db.refresh(billetera_usd)
    assert billetera_usd.saldo_actual == Decimal("985.00"), "Saldo en billetera USD debe ser 985 (1000 - 20 + 5)"

    cond = condicion_gasto(usuario.id, desde=hoy, hasta=hoy, moneda=Moneda.USD, hoy=hoy)
    rows = db.execute(
        select(Transaccion.monto).where(
            cond,
            Transaccion.categoria_id == categoria_supermercado.id,
            Transaccion.moneda == Moneda.USD,
        )
    ).scalars().all()
    gasto_neto_usd = sum(rows, Decimal("0.00"))
    assert gasto_neto_usd == Decimal("15.00"), "Gasto neto en USD debe ser 15 (20 - 5)"


# ==============================================================================
# M3: Reintegro de $25.000 "todavía no" sobre gasto de $100.000 y luego recibido
# Repetido para Débito, Efectivo y Transferencia
# ==============================================================================
@pytest.mark.parametrize("metodo", [MetodoPago.DEBITO, MetodoPago.EFECTIVO, MetodoPago.TRANSFERENCIA])
def test_m3_reintegro_pendiente_y_luego_recibido(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    metodo: MetodoPago,
):
    """M3 Reintegro de $25.000 'todavía no' sobre un gasto de $100.000:
    saldo 400.000, gasto 100.000, sin espejo;
    al marcarlo recibido hoy: saldo 425.000, gasto neto 75.000."""
    hoy = hoy_argentina()

    # 1. Compra de $100.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra M3 Supermercado",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=metodo,
        ),
    )

    # 2. Reintegro "todavía no" (recibido=False)
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=False,
    )

    # Validaciones estado pendiente: saldo 400.000, gasto 100.000, sin espejo
    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("400000.00"), "Saldo debe ser 400.000 (500k - 100k)"

    cond = condicion_gasto(usuario.id, desde=hoy, hasta=hoy, hoy=hoy)
    rows = db.execute(
        select(Transaccion.monto).where(
            cond,
            Transaccion.categoria_id == categoria_supermercado.id,
        )
    ).scalars().all()
    gasto_neto = sum(rows, Decimal("0.00"))
    assert gasto_neto == Decimal("100000.00"), "Gasto debe ser 100.000"

    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalars().all()
    assert len(espejos) == 0, "No debe existir espejo mientras esté pendiente"

    # 3. Al marcarlo recibido hoy
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )

    # Validaciones recibido hoy: saldo 425.000, gasto neto 75.000
    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("425000.00"), "Saldo debe ser 425.000 tras recibir el reintegro"

    rows_desp = db.execute(
        select(Transaccion.monto).where(
            cond,
            Transaccion.categoria_id == categoria_supermercado.id,
        )
    ).scalars().all()
    gasto_neto_desp = sum(rows_desp, Decimal("0.00"))
    assert gasto_neto_desp == Decimal("75000.00"), "Gasto neto debe ser 75.000 tras recibir el reintegro"
