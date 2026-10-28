"""
tests/test_transferencia_cotizacion.py — TE.11c: Pruebas de cálculo de monto destino, endpoint /transferencias/simular y alta de transferencias con cotización.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from fastapi import HTTPException
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
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import transferencia_service
from app.services.transferencia_service import calcular_monto_destino


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
        email="test_transf_cotiz@argentum.com",
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


def test_calcular_monto_destino_valores_exactos():
    # 1. ARS a USD: 150000 / 1450 = 103.4482... -> 103.45
    res1 = calcular_monto_destino(Decimal("150000"), Decimal("1450"), Moneda.ARS, Moneda.USD)
    assert res1 == Decimal("103.45")

    # 2. USD a ARS: 100 * 1420 = 142000.00
    res2 = calcular_monto_destino(Decimal("100"), Decimal("1420"), Moneda.USD, Moneda.ARS)
    assert res2 == Decimal("142000.00")

    # 3. USD a ARS: 10.25 * 1450.10 = 14863.525 -> 14863.53
    res3 = calcular_monto_destino(Decimal("10.25"), Decimal("1450.10"), Moneda.USD, Moneda.ARS)
    assert res3 == Decimal("14863.53")

    # 4. Misma moneda: ValueError
    with pytest.raises(ValueError) as exc:
        calcular_monto_destino(Decimal("1000"), Decimal("1"), Moneda.ARS, Moneda.ARS)
    assert str(exc.value) == "La cotización solo aplica entre pesos y dólares."


def test_endpoint_simular_transferencia(client: TestClient, db: Session, usuario_test: Usuario):
    # 1. ARS -> USD: monto 150000, cotiz 1450 -> 103.45 y 1450.0
    r1 = client.get("/transferencias/simular?monto=150000&cotizacion=1450&moneda_origen=ARS&moneda_destino=USD")
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["monto_destino"] == 103.45
    assert d1["cotizacion"] == 1450.0

    # 2. USD -> ARS: 100 a 1420 -> 142000.0
    r2 = client.get("/transferencias/simular?monto=100&cotizacion=1420&moneda_origen=USD&moneda_destino=ARS")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["monto_destino"] == 142000.0

    # 3. ARS a ARS -> 422
    r3 = client.get("/transferencias/simular?monto=1000&cotizacion=1&moneda_origen=ARS&moneda_destino=ARS")
    assert r3.status_code == 422
    assert "La cotización solo aplica entre pesos y dólares." in r3.text

    # 4. cotizacion=0 -> 422
    r4 = client.get("/transferencias/simular?monto=100&cotizacion=0&moneda_origen=ARS&moneda_destino=USD")
    assert r4.status_code == 422

    # 5. Sin usuario autenticado -> 401
    app.dependency_overrides.pop(get_current_user, None)
    with TestClient(app) as unauth_client:
        r5 = unauth_client.get("/transferencias/simular?monto=100&cotizacion=1450&moneda_origen=ARS&moneda_destino=USD")
        assert r5.status_code == 401

    # 6. GET /transferencias/<uuid> sigue respondiendo como hoy y no es tapada por /simular
    app.dependency_overrides[get_current_user] = lambda: usuario_test
    # Crear una transferencia previa
    b_ars = Billetera(id=uuid4(), usuario_id=usuario_test.id, nombre="ARS", moneda=Moneda.ARS, saldo_actual=Decimal("1000"))
    b_usd = Billetera(id=uuid4(), usuario_id=usuario_test.id, nombre="USD", moneda=Moneda.USD, saldo_actual=Decimal("100"))
    db.add(b_ars)
    db.add(b_usd)
    t_existente = TransferenciaInterna(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_origen_id=b_ars.id,
        billetera_destino_id=b_usd.id,
        monto=Decimal("1450.00"),
        moneda=Moneda.ARS,
        monto_origen=Decimal("1450.00"),
        monto_destino=Decimal("1.00"),
        moneda_origen=Moneda.ARS,
        moneda_destino=Moneda.USD,
        cotizacion=Decimal("1450.0000"),
        fecha=date(2026, 10, 27),
    )
    db.add(t_existente)
    db.commit()

    r6 = client.get(f"/transferencias/{t_existente.id}")
    assert r6.status_code == 200, r6.text
    assert r6.json()["id"] == str(t_existente.id)


def test_crear_transferencia_con_cotizacion(db: Session, usuario_test: Usuario):
    b_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("200000.00"),
        saldo_inicial=Decimal("200000.00"),
        # Fecha del saldo fija: con la fecha real de creación, la transferencia del 27/10 quedaba anterior a la foto
        fecha_saldo=date(2026, 10, 1),
        estado=EstadoBilletera.ACTIVA,
    )
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera USD",
        moneda=Moneda.USD,
        saldo_actual=Decimal("0.00"),
        saldo_inicial=Decimal("0.00"),
        fecha_saldo=date(2026, 10, 1),
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_ars)
    db.add(b_usd)
    db.commit()

    # 1. Crear transferencia con monto 150000, cotizacion 1450 y sin monto_destino
    data1 = TransferenciaInternaCreate(
        billetera_origen_id=b_ars.id,
        billetera_destino_id=b_usd.id,
        monto=Decimal("150000.00"),
        moneda=Moneda.ARS,
        cotizacion=Decimal("1450.0000"),
        fecha=date(2026, 10, 27),
    )
    t1 = transferencia_service.crear_transferencia(db, usuario_test.id, data1)

    db.refresh(b_ars)
    db.refresh(b_usd)
    assert t1.monto_destino == Decimal("103.45")
    assert t1.cotizacion == Decimal("1450.0000")
    assert b_ars.saldo_actual == Decimal("50000.00")
    assert b_usd.saldo_actual == Decimal("103.45")

    # 2. Sin cotización ni monto_destino: 400 con el texto de hoy
    data2 = TransferenciaInternaCreate(
        billetera_origen_id=b_ars.id,
        billetera_destino_id=b_usd.id,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 27),
    )
    with pytest.raises(HTTPException) as exc2:
        transferencia_service.crear_transferencia(db, usuario_test.id, data2)
    assert exc2.value.status_code == 400
    assert exc2.value.detail == "Para transferencias entre billeteras de distinta moneda debes ingresar el monto recibido en destino."

    # 3. Con monto_destino 100 y sin cotización: deriva cotización (150000 / 100 = 1500.0000)
    # Reabastecer saldo para la prueba
    b_ars.saldo_actual = Decimal("200000.00")
    db.commit()

    data3 = TransferenciaInternaCreate(
        billetera_origen_id=b_ars.id,
        billetera_destino_id=b_usd.id,
        monto=Decimal("150000.00"),
        moneda=Moneda.ARS,
        monto_destino=Decimal("100.00"),
        fecha=date(2026, 10, 27),
    )
    t3 = transferencia_service.crear_transferencia(db, usuario_test.id, data3)
    assert t3.cotizacion == Decimal("1500.0000")

