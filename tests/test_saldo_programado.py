from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.saldo_programado_service import (
    _job_aplicar_movimientos_con_fecha_llegada,
    aplicar_movimientos_con_fecha_llegada,
)
from app.services.transaccion_service import (
    actualizar_transaccion,
    crear_transaccion,
    eliminar_transaccion,
)

FECHA_BASE = date(2026, 10, 12)


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
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(
            JSONB._compiler_dispatcher, "specs"
        ):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
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


def _set_hoy(monkeypatch, fecha: date):
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.transaccion_service._hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.conciliacion_service.hoy_argentina", lambda: fecha)


def _crear_entorno_prueba(
    db: Session, saldo: Decimal = Decimal("100000.00")
) -> tuple[Usuario, Billetera, Categoria]:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera ARS Test",
        moneda=Moneda.ARS,
        saldo_inicial=saldo,
        saldo_actual=saldo,
    )
    c = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([u, b, c])
    db.commit()
    db.refresh(u)
    db.refresh(b)
    db.refresh(c)
    return u, b, c


# 1. Egreso débito de 10000.00 con fecha hoy+3: saldo 100000.00 y saldo_aplicado False.
# Job con hoy+2: nada cambia. Job con hoy+3: saldo 90000.00 y saldo_aplicado True. Job otra vez: sigue en 90000.00.
def test_1_egreso_debito_fecha_futura_y_job(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_futura = FECHA_BASE + timedelta(days=3)
    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=fecha_futura,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=c.id,
        ),
    )
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False

    # Job con hoy+2: nada cambia
    _set_hoy(monkeypatch, FECHA_BASE + timedelta(days=2))
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False

    # Job con hoy+3: saldo 90000.00 y saldo_aplicado True
    _set_hoy(monkeypatch, FECHA_BASE + timedelta(days=3))
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("90000.00")
    assert tx.saldo_aplicado is True

    # Job otra vez: sigue en 90000.00 (idempotencia)
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("90000.00")


# 2. Ese mismo egreso borrado antes de su fecha: saldo 100000.00.
def test_2_egreso_futuro_borrado_antes_de_su_fecha(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_futura = FECHA_BASE + timedelta(days=3)
    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=fecha_futura,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=c.id,
        ),
    )
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False

    # Borrado antes de su fecha
    eliminar_transaccion(db_session, u.id, tx.id)
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("100000.00")


# 3. Ese mismo egreso borrado después de que el job lo aplicó: saldo vuelve a 100000.00.
def test_3_egreso_futuro_borrado_despues_de_que_job_lo_aplico(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_futura = FECHA_BASE + timedelta(days=3)
    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=fecha_futura,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=c.id,
        ),
    )

    # Job en la fecha hoy+3
    _set_hoy(monkeypatch, FECHA_BASE + timedelta(days=3))
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("90000.00")
    assert tx.saldo_aplicado is True

    # Borrar después de que el job lo aplicó: saldo vuelve a 100000.00
    eliminar_transaccion(db_session, u.id, tx.id)
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("100000.00")


# 4. Egreso futuro editado a 15000.00 antes de su fecha: saldo 100000.00; el job en la fecha deja 85000.00.
def test_4_egreso_futuro_editado_antes_de_su_fecha(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_futura = FECHA_BASE + timedelta(days=3)
    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=fecha_futura,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=c.id,
        ),
    )

    # Editar monto a 15000.00 antes de su fecha
    actualizar_transaccion(
        db_session,
        u.id,
        tx.id,
        TransaccionUpdate(monto=Decimal("15000.00")),
    )
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False

    # El job en la fecha deja 85000.00
    _set_hoy(monkeypatch, fecha_futura)
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("85000.00")
    assert tx.saldo_aplicado is True


# 5. Egreso con fecha de hoy: saldo 90000.00 en el momento y saldo_aplicado True.
def test_5_egreso_fecha_de_hoy_impacto_inmediato(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=FECHA_BASE,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=c.id,
        ),
    )
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("90000.00")
    assert tx.saldo_aplicado is True


# 6. Egreso futuro editado a fecha de hoy: aplica en el momento (90000.00) y el job después no cambia nada.
def test_6_egreso_futuro_editado_a_fecha_de_hoy(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_futura = FECHA_BASE + timedelta(days=3)
    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=fecha_futura,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=c.id,
        ),
    )
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False

    # Editar a fecha de hoy: aplica en el momento (90000.00)
    actualizar_transaccion(
        db_session,
        u.id,
        tx.id,
        TransaccionUpdate(fecha=FECHA_BASE),
    )
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("90000.00")
    assert tx.saldo_aplicado is True

    # El job después no cambia nada
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("90000.00")


# 7. Ingreso de 5000.00 con fecha hoy+1: el job en esa fecha deja 105000.00.
def test_7_ingreso_futuro_y_job(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    cat_ingreso = Categoria(
        id=uuid4(),
        nombre="Sueldo",
        tipo=TipoCategoria.INGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db_session.add(cat_ingreso)
    db_session.commit()

    fecha_futura = FECHA_BASE + timedelta(days=1)
    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=fecha_futura,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            billetera_id=b.id,
            categoria_id=cat_ingreso.id,
        ),
    )
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False

    # El job en esa fecha deja 105000.00
    _set_hoy(monkeypatch, fecha_futura)
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    db_session.refresh(tx)
    assert b.saldo_actual == Decimal("105000.00")
    assert tx.saldo_aplicado is True


# 8. Compra a crédito y egreso PENDIENTE con fecha pasada: el job no los toca.
def test_8_credito_y_pendiente_no_tocados_por_job(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_pasada = FECHA_BASE - timedelta(days=2)

    # Compra a crédito con fecha pasada
    tx_credito = Transaccion(
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=c.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("8000.00"),
        moneda=Moneda.ARS,
        fecha=fecha_pasada,
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        saldo_aplicado=False,  # Forzar False para verificar que el job lo ignore
    )
    # Egreso PENDIENTE con fecha pasada
    tx_pendiente = Transaccion(
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=c.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("4000.00"),
        moneda=Moneda.ARS,
        fecha=fecha_pasada,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        saldo_aplicado=False,  # Forzar False para verificar que el job lo ignore
    )
    db_session.add_all([tx_credito, tx_pendiente])
    db_session.commit()

    # El job no los toca
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    db_session.refresh(tx_credito)
    db_session.refresh(tx_pendiente)

    assert b.saldo_actual == Decimal("100000.00")
    assert tx_credito.saldo_aplicado is False
    assert tx_pendiente.saldo_aplicado is False


# 9. Después del job del caso 1: calcular_saldo_teorico(billetera) == saldo_actual.
def test_9_calcular_saldo_teorico_coincide_tras_job(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_futura = FECHA_BASE + timedelta(days=3)
    tx = crear_transaccion(
        db_session,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            fecha=fecha_futura,
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=c.id,
        ),
    )

    # Job en la fecha hoy+3
    _set_hoy(monkeypatch, fecha_futura)
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)

    teorico = calcular_saldo_teorico(db_session, b.id, hasta=fecha_futura)
    assert teorico == b.saldo_actual == Decimal("90000.00")


# 10. Una Transaccion insertada directo con db.add (como hacen los otros servicios), fecha de hoy,
# débito de 7000.00, con el saldo bajado a mano a 93000.00: queda con saldo_aplicado True por defecto
# y el job no la toca (saldo sigue en 93000.00).
def test_10_transaccion_directa_db_add_default_true(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    tx = Transaccion(
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=c.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("7000.00"),
        moneda=Moneda.ARS,
        fecha=FECHA_BASE,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    b.saldo_actual = Decimal("93000.00")
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    db_session.refresh(b)

    # Queda con saldo_aplicado True por defecto
    assert tx.saldo_aplicado is True

    # El job no la toca (saldo sigue en 93000.00)
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    assert b.saldo_actual == Decimal("93000.00")


# 11. Egreso débito de 10000.00 con fecha hoy-1 y saldo_aplicado False puesto a mano (como Usuario_05
# tras la migración), saldo 100000.00. Editar solo la descripción: saldo_aplicado sigue False
# y el saldo sigue en 100000.00. Después el job: saldo 90000.00 y saldo_aplicado True.
def test_11_egreso_pasado_no_aplicado_editar_descripcion_y_job(db_session: Session, monkeypatch):
    _set_hoy(monkeypatch, FECHA_BASE)
    u, b, c = _crear_entorno_prueba(db_session, Decimal("100000.00"))

    fecha_pasada = FECHA_BASE - timedelta(days=1)
    tx = Transaccion(
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=c.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=fecha_pasada,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Original",
        saldo_aplicado=False,  # Puesto a mano como queda tras la migración
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    db_session.refresh(b)

    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False

    # Editar solo la descripción: saldo_aplicado sigue False y el saldo sigue en 100000.00
    actualizar_transaccion(
        db_session,
        u.id,
        tx.id,
        TransaccionUpdate(descripcion="Descripción actualizada"),
    )
    db_session.refresh(b)
    db_session.refresh(tx)

    assert b.saldo_actual == Decimal("100000.00")
    assert tx.saldo_aplicado is False
    assert tx.descripcion == "Descripción actualizada"

    # Después el job: saldo 90000.00 y saldo_aplicado True
    _job_aplicar_movimientos_con_fecha_llegada(db_session)
    db_session.refresh(b)
    db_session.refresh(tx)

    assert b.saldo_actual == Decimal("90000.00")
    assert tx.saldo_aplicado is True

