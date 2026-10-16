"""
tests/test_debito_usd.py — Pruebas de egreso en dólares pagado con débito en pesos y sugerencia de cotización.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.transaccion import MetodoPago, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services.definiciones_service import condicion_gasto
from app.services import transaccion_service
from app.utils.fecha import hoy_argentina


# Setup SQLite compatibility para pruebas unitarias
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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    usuario = Usuario(
        id=uuid4(),
        email="test_debito_usd@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_test: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_test

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_sugerencia_pesos_con_cotizacion(client: TestClient, db: Session):
    """
    cotización oficial venta de hoy 1.000: GET sugerencia-pesos con monto_usd 20
    → cotizacion 1000, percepcion 30, monto_pesos 26000.00.
    """
    # Insertar cotización oficial para la fecha de hoy
    cotiz = CotizacionDolar(
        id=uuid4(),
        fecha=hoy_argentina(),
        tipo="oficial",
        compra=Decimal("950.0000"),
        venta=Decimal("1000.0000"),
        promedio=Decimal("975.0000"),
    )
    db.add(cotiz)
    db.commit()

    response = client.get("/transacciones/sugerencia-pesos?monto_usd=20")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["cotizacion"] == 1000
    assert data["percepcion"] == 30
    assert data["monto_pesos"] == 26000.00


def test_sugerencia_pesos_sin_cotizacion(client: TestClient, db: Session):
    """
    sin cotización: monto_pesos null y cotizacion null.
    """
    response = client.get("/transacciones/sugerencia-pesos?monto_usd=20")
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["cotizacion"] is None
    assert data["percepcion"] == 30
    assert data["monto_pesos"] is None


def test_alta_egreso_ars_con_monto_original_usd(db: Session, usuario_test: Usuario):
    """
    egreso ARS de 26.500 en billetera ARS con 100.000, moneda_original USD y monto_original 20:
    saldo 73500.00, cotizacion_aplicada 1325.0000, tipo_dolar_usado "tarjeta",
    y cuenta como gasto de 26500.00 en pesos (condicion_gasto).
    """
    billetera_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Banco Galicia Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        saldo_inicial=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA,
        es_principal=True,
        es_efectivo=False,
    )
    db.add(billetera_ars)

    categoria = Categoria(
        id=uuid4(),
        nombre="Suscripciones",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(categoria)
    db.commit()

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("26500.00"),
        moneda=Moneda.ARS,
        fecha=hoy_argentina(),
        descripcion="Suscripción exterior con débito",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
        moneda_original=Moneda.USD,
        monto_original=Decimal("20.00"),
    )

    tx = transaccion_service.crear_transaccion(db, usuario_test.id, tx_data)

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("73500.00")
    assert tx.cotizacion_aplicada == Decimal("1325.0000")
    assert tx.tipo_dolar_usado == "tarjeta"

    # Verificar que cuenta como gasto de 26500.00 en pesos mediante condicion_gasto
    stmt = select(func.coalesce(func.sum(Transaccion.monto), Decimal("0"))).where(
        Transaccion.usuario_id == usuario_test.id,
        condicion_gasto(usuario_id=usuario_test.id, moneda=Moneda.ARS, hoy=hoy_argentina()),
    )
    total_gasto = db.execute(stmt).scalar()
    assert total_gasto == Decimal("26500.00")


def test_error_moneda_original_usd_sin_monto_original(db: Session, usuario_test: Usuario):
    """
    con moneda_original USD y sin monto_original: 400 con el texto exacto.
    """
    billetera_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Banco Galicia Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        saldo_inicial=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA,
        es_principal=True,
        es_efectivo=False,
    )
    db.add(billetera_ars)

    categoria = Categoria(
        id=uuid4(),
        nombre="Suscripciones",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(categoria)
    db.commit()

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("26500.00"),
        moneda=Moneda.ARS,
        fecha=hoy_argentina(),
        descripcion="Suscripción exterior sin monto en dólares",
        categoria_id=categoria.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
        moneda_original=Moneda.USD,
        monto_original=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        transaccion_service.crear_transaccion(db, usuario_test.id, tx_data)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Falta el monto en dólares."
