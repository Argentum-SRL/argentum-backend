"""
tests/test_sugerencia_pesos_tarjeta.py — TE.11b: Pruebas de sugerencia de pesos y dólar tarjeta en el backend.
"""
from __future__ import annotations

from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.usuario import AuthProvider, EstadoUsuario, RolUsuario, Usuario
from app.utils.fecha import hoy_argentina


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
    u = Usuario(
        id=uuid4(),
        email="test_sugerencia_tarjeta@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
    )
    db.add(u)
    db.commit()
    return u


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


def test_sugerencia_pesos_dolar_tarjeta(client: TestClient, db: Session):
    # Caso 1: Venta 1000.05, monto_usd 1 -> 1.300,065 sube con ROUND_HALF_UP a 1300.07
    c1 = CotizacionDolar(
        id=uuid4(),
        fecha=hoy_argentina(),
        tipo="oficial",
        compra=Decimal("950.00"),
        venta=Decimal("1000.05"),
        promedio=Decimal("975.00"),
    )
    db.add(c1)
    db.commit()

    r1 = client.get("/transacciones/sugerencia-pesos?monto_usd=1")
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["monto_pesos"] == 1300.07
    assert d1["cotizacion_tarjeta"] == 1300.07

    # Caso 2: Venta 1450, monto_usd 100 -> monto_pesos 188500.0, cotizacion_tarjeta 1885.0
    c1.venta = Decimal("1450.00")
    db.commit()

    r2 = client.get("/transacciones/sugerencia-pesos?monto_usd=100")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["monto_pesos"] == 188500.0
    assert d2["cotizacion_tarjeta"] == 1885.0

    # Caso 3: Sin cotización -> cotizacion_tarjeta null
    db.delete(c1)
    db.commit()

    r3 = client.get("/transacciones/sugerencia-pesos?monto_usd=10")
    assert r3.status_code == 200, r3.text
    d3 = r3.json()
    assert d3["cotizacion"] is None
    assert d3["monto_pesos"] is None
    assert d3["cotizacion_tarjeta"] is None

