"""
tests/test_reintegros_presupuesto.py — Pruebas de impacto de reintegros en el presupuesto (fase5c_fix1).

Presupuesto (presupuesto activo mensual de Supermercado, límite 200.000, ARS):
- P1 Compra de $100.000: usado 100.000. Reintegro recibido de $25.000: usado 75.000.
- P2 Quitar el reintegro: usado 100.000.
- P3 Editar el reintegro de 25.000 a 30.000: usado 70.000.
- P4 Borrar la compra con reintegro: usado 0.
- P5 Reintegro pendiente ("todavía no"): usado 100.000.
- P6 Con presupuesto de límite 100.000: la compra de $100.000 genera la alerta de agotado como hoy; al llegar el reintegro no se crea ninguna alerta nueva.
- P7 Crédito: mismo caso que P1 pero con tarjeta; el esperado es la paridad con una cuota hija normal según el crudo _10 (mostrar el resultado, no inventar).
- P8 Compra el 28 de un mes y reintegro recibido el 2 del mes siguiente, con presupuesto mensual: CRUDO SOLAMENTE de monto_usado del mes siguiente (sin esperado, sin cambiar código; ver CUÁNDO FRENAR).
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
from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import reintegro_service, transaccion_service
from app.services.presupuesto_service import calcular_gasto_en_periodo, obtener_presupuesto
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
        email="test_presupuesto_fix@argentum.com",
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


@pytest.fixture(name="tarjeta_credito")
def tarjeta_credito_fixture(db: Session, usuario: Usuario, billetera_ars: Billetera):
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
# P1: Compra de $100.000: usado 100.000. Reintegro recibido de $25.000: usado 75.000.
# ==============================================================================
def test_p1_compra_y_reintegro_recibido(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    presupuesto_supermercado: Presupuesto,
):
    hoy = hoy_argentina()
    periodo = presupuesto_supermercado.periodos[0]

    # Compra de $100.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra P1 Supermercado",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    db.refresh(periodo)
    assert periodo.monto_usado == Decimal("100000.00"), "Tras compra de $100.000, monto_usado debe ser 100.000"

    # Reintegro recibido de $25.000 el mismo día
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )
    db.refresh(periodo)
    assert periodo.monto_usado == Decimal("75000.00"), "Tras reintegro de $25.000, monto_usado debe ser 75.000"


# ==============================================================================
# P2: Quitar el reintegro: usado 100.000.
# ==============================================================================
def test_p2_quitar_reintegro(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    presupuesto_supermercado: Presupuesto,
):
    hoy = hoy_argentina()
    periodo = presupuesto_supermercado.periodos[0]

    # Compra y reintegro recibido
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra P2",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )

    # Quitar el reintegro
    reintegro_service.eliminar_reintegro(db, usuario.id, compra.id)
    db.refresh(periodo)
    assert periodo.monto_usado == Decimal("100000.00"), "Al quitar el reintegro, monto_usado debe volver a 100.000"


# ==============================================================================
# P3: Editar el reintegro de 25.000 a 30.000: usado 70.000.
# ==============================================================================
def test_p3_editar_reintegro(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    presupuesto_supermercado: Presupuesto,
):
    hoy = hoy_argentina()
    periodo = presupuesto_supermercado.periodos[0]

    # Compra de $100.000 con reintegro recibido de $25.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra P3",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )

    # Editar el reintegro a $30.000
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("30000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )
    db.refresh(periodo)
    assert periodo.monto_usado == Decimal("70000.00"), "Al editar reintegro a 30.000, monto_usado debe ser 70.000"


# ==============================================================================
# P4: Borrar la compra con reintegro: usado 0.
# ==============================================================================
def test_p4_borrar_compra_con_reintegro(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    presupuesto_supermercado: Presupuesto,
):
    hoy = hoy_argentina()
    periodo = presupuesto_supermercado.periodos[0]

    # Compra de $100.000 con reintegro recibido de $25.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra P4",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )

    # Borrar la compra
    transaccion_service.eliminar_transaccion(db, usuario.id, compra.id)
    db.refresh(periodo)
    assert periodo.monto_usado == Decimal("0.00"), "Al borrar la compra con reintegro, monto_usado debe quedar en 0"


# ==============================================================================
# P5: Reintegro pendiente ("todavía no"): usado 100.000.
# ==============================================================================
def test_p5_reintegro_pendiente(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    presupuesto_supermercado: Presupuesto,
):
    hoy = hoy_argentina()
    periodo = presupuesto_supermercado.periodos[0]

    # Compra de $100.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra P5",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    # Reintegro pendiente ("todavía no")
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=False,
    )
    db.refresh(periodo)
    assert periodo.monto_usado == Decimal("100000.00"), "Con reintegro pendiente, monto_usado debe ser 100.000"


# ==============================================================================
# P6: Con presupuesto de límite 100.000: la compra de $100.000 genera la alerta
#     de agotado como hoy; al llegar el reintegro no se crea ninguna alerta nueva.
# ==============================================================================
def test_p6_presupuesto_limite_100k_alerta(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
):
    hoy = hoy_argentina()
    # Presupuesto con límite exacto de 100.000
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Presu 100k",
        monto=Decimal("100000.00"),
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

    fecha_inicio = date(hoy.year, hoy.month, 1)
    fecha_fin = date(hoy.year, hoy.month + 1, 1) if hoy.month < 12 else date(hoy.year + 1, 1, 1)

    periodo = PeriodoPresupuesto(
        presupuesto_id=presu.id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db.add(periodo)
    db.commit()

    # Compra de $100.000: genera alerta de agotado
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra 100k",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    db.refresh(periodo)
    assert periodo.monto_usado == Decimal("100000.00")

    notifs_iniciales = db.execute(
        select(Notificacion).where(Notificacion.usuario_id == usuario.id)
    ).scalars().all()
    cant_notifs_tras_compra = len(notifs_iniciales)
    assert cant_notifs_tras_compra >= 1, "La compra al 100% debió generar alerta de presupuesto"

    # Al llegar el reintegro de $25.000
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )

    notifs_desp = db.execute(
        select(Notificacion).where(Notificacion.usuario_id == usuario.id)
    ).scalars().all()
    assert len(notifs_desp) == cant_notifs_tras_compra, "Al llegar el reintegro no debe crearse ninguna alerta nueva"


# ==============================================================================
# P7: Crédito: mismo caso que P1 pero con tarjeta; el esperado es la paridad
#     con una cuota hija normal según el crudo _10 (mostrar el resultado, no inventar).
# ==============================================================================
def test_p7_credito(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    tarjeta_credito: TarjetaCredito,
    presupuesto_supermercado: Presupuesto,
):
    """P7 Crédito: paridad con cuota hija normal de crédito según crudo _10:
    Las transacciones de crédito crean padre es_padre_cuotas=True y cuota hija PENDIENTE.
    Crudo _10 demostró que una compra normal con tarjeta de crédito no llama a
    registrar_impacto_presupuesto en la creación, manteniendo periodo.monto_usado en BD
    en su valor previo (0.00), mientras que el cálculo dinámico (obtener_presupuesto /
    calcular_gasto_en_periodo) refleja el gasto neto en el ciclo."""
    hoy = hoy_argentina()
    periodo = presupuesto_supermercado.periodos[0]

    # Compra con tarjeta de crédito de $100.000
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra Tarjeta Supermercado",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            tarjeta_id=tarjeta_credito.id,
            metodo_pago=MetodoPago.CREDITO,
        ),
    )

    # Reintegro recibido de $25.000
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy,
    )
    db.refresh(periodo)

    # Paridad con cuota hija normal según crudo _10:
    # 1. En BD (columna cruda), no se llama registrar_impacto_presupuesto en crédito
    assert periodo.monto_usado == Decimal("0.00"), "Paridad crudo _10: periodo.monto_usado directo en BD permanece 0.00"

    # 2. En cálculo dinámico (gasto en período del mes actual)
    presu_cat = presupuesto_supermercado.categorias[0]
    gasto_calculado = calcular_gasto_en_periodo(
        db,
        usuario.id,
        [presu_cat],
        periodo.fecha_inicio,
        periodo.fecha_fin,
        Moneda.ARS,
    )
    assert gasto_calculado == Decimal("75000.00"), "El gasto calculado neto en el período debe ser 75.000 (100k - 25k)"


# ==============================================================================
# P8: Compra el 28 de un mes y reintegro recibido el 2 del mes siguiente, con presupuesto mensual:
#     CRUDO SOLAMENTE de monto_usado del mes siguiente (sin esperado, sin cambiar código; ver CUÁNDO FRENAR).
# ==============================================================================
def test_p8_periodos_distintos(
    db: Session,
    usuario: Usuario,
    categoria_supermercado: Categoria,
    billetera_ars: Billetera,
    presupuesto_supermercado: Presupuesto,
):
    """P8: Compra el 28 de un mes y reintegro recibido el 2 del mes siguiente.
    Sin valor esperado fijo, solo registra / informa el valor de monto_usado del mes siguiente."""
    # Mes 1: Septiembre 2026. Mes 2: Octubre 2026.
    periodo_sep = PeriodoPresupuesto(
        presupuesto_id=presupuesto_supermercado.id,
        fecha_inicio=date(2026, 9, 1),
        fecha_fin=date(2026, 9, 30),
        monto_limite=Decimal("200000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db.add(periodo_sep)

    periodo_oct = PeriodoPresupuesto(
        presupuesto_id=presupuesto_supermercado.id,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2026, 10, 31),
        monto_limite=Decimal("200000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db.add(periodo_oct)
    db.commit()

    # Compra el 28 de septiembre
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=date(2026, 9, 28),
            descripcion="Compra 28/09",
            categoria_id=categoria_supermercado.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    # Reintegro recibido el 2 de octubre
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 2),
    )

    db.refresh(periodo_oct)
    presu_cat = presupuesto_supermercado.categorias[0]
    gasto_oct = calcular_gasto_en_periodo(
        db,
        usuario.id,
        [presu_cat],
        date(2026, 10, 1),
        date(2026, 10, 31),
        Moneda.ARS,
    )

    # Registro en salida del crudo (sin esperado, sin cambiar código)
    print(f"\n[P8 REGISTRO CRUDO]")
    print(f"  Periodo Octubre monto_usado (columna cruda): {periodo_oct.monto_usado}")
    print(f"  Periodo Octubre calcular_gasto_en_periodo: {gasto_oct}")
    assert True
