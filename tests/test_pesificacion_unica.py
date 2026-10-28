"""
tests/test_pesificacion_unica.py — TE.11a: Pruebas de la función unificada de pesificación y endpoint simular-pesificación.
"""
from __future__ import annotations

from datetime import date
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
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
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
from app.services import pago_resumen_service
from app.services.pago_resumen_service import calcular_montos_pesificacion


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
        email="test_pesif_unica@argentum.com",
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


def test_calcular_montos_pesificacion_casos_exactos():
    # 1. Caso base: USD 100, cotiz 1450, percep 30
    res1 = calcular_montos_pesificacion(Decimal("100"), Decimal("1450"), Decimal("30"))
    assert res1["monto_convertido"] == Decimal("145000.00")
    assert res1["monto_percepcion"] == Decimal("43500.00")
    assert res1["total"] == Decimal("188500.00")

    # 2. Con monto_pesos personalizado 150000
    res2 = calcular_montos_pesificacion(
        Decimal("100"), Decimal("1450"), Decimal("30"), monto_pesos=Decimal("150000")
    )
    assert res2["monto_convertido"] == Decimal("150000.00")
    assert res2["monto_percepcion"] == Decimal("45000.00")
    assert res2["total"] == Decimal("195000.00")

    # 3. Con monto_percepcion personalizado 0
    res3 = calcular_montos_pesificacion(
        Decimal("100"), Decimal("1450"), Decimal("30"), monto_percepcion=Decimal("0")
    )
    assert res3["monto_convertido"] == Decimal("145000.00")
    assert res3["monto_percepcion"] == Decimal("0.00")
    assert res3["total"] == Decimal("145000.00")

    # 4. Caso con medio centavo que sube por ROUND_HALF_UP (14.863,525 -> 14863.53)
    res4 = calcular_montos_pesificacion(Decimal("10.25"), Decimal("1450.10"), Decimal("30"))
    assert res4["monto_convertido"] == Decimal("14863.53")
    assert res4["monto_percepcion"] == Decimal("4459.06")
    assert res4["total"] == Decimal("19322.59")


def _crear_tarjeta_usd(db: Session, u: Usuario) -> tuple[Billetera, TarjetaCredito]:
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Galicia Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000.00"),
        saldo_inicial=Decimal("500000.00"),
        estado=EstadoBilletera.ACTIVA,
        es_principal=True,
    )
    db.add(b)

    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Visa Signature USD",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=25,
        percepcion_moneda_extranjera=Decimal("30.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(t)

    cat_banco = Categoria(id=uuid4(), nombre="Banco", tipo=TipoCategoria.EGRESO)
    db.add(cat_banco)
    sub_tc = Subcategoria(id=uuid4(), categoria_id=cat_banco.id, nombre="Tarjetas de crédito")
    sub_imp = Subcategoria(id=uuid4(), categoria_id=cat_banco.id, nombre="Impuestos")
    db.add(sub_tc)
    db.add(sub_imp)

    db.commit()
    return b, t


def test_endpoint_simular_pesificacion(client: TestClient, db: Session, usuario_test: Usuario):
    b, t = _crear_tarjeta_usd(db, usuario_test)

    # 1. monto_usd=100&cotizacion=1450
    r1 = client.get(f"/tarjetas/{t.id}/simular-pesificacion?monto_usd=100&cotizacion=1450")
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1["cotizacion_oficial"] == 1450.0
    assert d1["cotizacion_disponible"] is True
    assert d1["monto_convertido_ars"] == 145000.0
    assert d1["monto_percepcion_ars"] == 43500.0
    assert d1["total_estimado_ars"] == 188500.0

    # 2. Con &monto_pesos=150000
    r2 = client.get(f"/tarjetas/{t.id}/simular-pesificacion?monto_usd=100&cotizacion=1450&monto_pesos=150000")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["monto_convertido_ars"] == 150000.0
    assert d2["monto_percepcion_ars"] == 45000.0
    assert d2["total_estimado_ars"] == 195000.0

    # 3. monto_usd=10.25&cotizacion=1450.10
    r3 = client.get(f"/tarjetas/{t.id}/simular-pesificacion?monto_usd=10.25&cotizacion=1450.10")
    assert r3.status_code == 200, r3.text
    d3 = r3.json()
    assert d3["monto_convertido_ars"] == 14863.53
    assert d3["monto_percepcion_ars"] == 4459.06
    assert d3["total_estimado_ars"] == 19322.59

    # 4. cotizacion=0 -> 422
    r4 = client.get(f"/tarjetas/{t.id}/simular-pesificacion?monto_usd=100&cotizacion=0")
    assert r4.status_code == 422


def test_pagar_resumen_tarjeta_pesificando_personalizado(db: Session, usuario_test: Usuario):
    b, t = _crear_tarjeta_usd(db, usuario_test)

    cat_banco = db.query(Categoria).filter(Categoria.nombre == "Banco").first()
    sub_tc = db.query(Subcategoria).filter(Subcategoria.nombre == "Tarjetas de crédito").first()

    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=b.id,
        tarjeta_id=t.id,
        categoria_id=cat_banco.id,
        subcategoria_id=sub_tc.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10.25"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 5),
        descripcion="Servicio exterior",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_padre)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario_test.id,
        transaccion_padre_id=tx_padre.id,
        tarjeta_id=t.id,
        descripcion="Servicio exterior",
        monto_total=Decimal("10.25"),
        total_financiado=Decimal("10.25"),
        cantidad_cuotas=1,
        tiene_interes=False,
        moneda=Moneda.USD,
    )
    db.add(grupo)
    db.flush()

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=b.id,
        tarjeta_id=t.id,
        categoria_id=cat_banco.id,
        subcategoria_id=sub_tc.id,
        grupo_cuotas_id=grupo.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10.25"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 25),
        descripcion="Servicio exterior (Cuota 1/1)",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db.add(tx_hija)
    db.flush()

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        monto_proyectado=Decimal("10.25"),
        monto_real=None,
        fecha_vencimiento=date(2026, 10, 25),
        pagada=False,
    )
    db.add(cuota)
    db.commit()

    tx_pago = pago_resumen_service.pagar_resumen_tarjeta(
        db=db,
        usuario_id=usuario_test.id,
        tarjeta_id=t.id,
        fecha_pago=date(2026, 10, 25),
        fecha_resumen=date(2026, 10, 25),
        monto=Decimal("10.25"),
        moneda=Moneda.USD,
        billetera_id=b.id,
        pesificar=True,
        cotizacion_personalizada=Decimal("1450.10"),
    )

    assert tx_pago.monto == Decimal("14863.53")
    assert getattr(tx_pago, "monto_percepcion") == Decimal("4459.06")
