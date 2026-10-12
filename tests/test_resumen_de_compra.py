"""
Tests para endpoint GET /tarjetas/{tarjeta_id}/resumen-de-compra (Fase E1 Tarjeta Resumen).
Verifica:
1. Compra 2026-09-20 -> fecha_cierre 2026-09-24, fecha_vencimiento 2026-10-07.
2. Compra 2026-09-26 -> fecha_cierre 2026-10-24, fecha_vencimiento 2026-11-09 (el 07/11 es sábado).
3. Compra 2026-09-20 con proximo_resumen=true -> fecha_cierre 2026-10-24, fecha_vencimiento 2026-11-09.
4. Compra el mismo día del cierre, 2026-09-24 -> fecha_cierre 2026-09-24, fecha_vencimiento 2026-10-07.
5. Tarjeta de otro usuario -> 404.
6. Crear con transaccion_service.crear_transaccion una compra a crédito en 1 cuota con fecha 2026-09-26:
   la fecha de vencimiento de su cuota es igual a la fecha_vencimiento que da el endpoint para esa fecha (2026-11-09).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.main import app
from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import MetodoPago, TipoTransaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import transaccion_service


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

    # Parchear feriados para que devuelva lista vacía y no requiera red ni BD externa
    monkeypatch.setattr("app.services.dias_habiles_service._get_feriados_cached_sync", lambda anio: [])

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


@pytest.fixture(name="escenario")
def escenario_fixture(db: Session):
    usuario = Usuario(
        id=uuid4(),
        email="test_user@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(usuario)

    otro_usuario = Usuario(
        id=uuid4(),
        email="otro_user@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(otro_usuario)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Sueldo",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        es_inversion=False,
    )
    db.add(billetera)

    # Tarjeta en pesos con cierre día 24 y vencimiento día 7
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa Santander",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=24,
        dia_vencimiento=7,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)

    # Tarjeta de otro usuario
    billetera_otro = Billetera(
        id=uuid4(),
        usuario_id=otro_usuario.id,
        nombre="Cuenta Sueldo Otro",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        es_inversion=False,
    )
    db.add(billetera_otro)

    tarjeta_otro = TarjetaCredito(
        id=uuid4(),
        usuario_id=otro_usuario.id,
        billetera_id=billetera_otro.id,
        nombre="Mastercard Otro",
        red=RedTarjeta.MASTERCARD,
        moneda=Moneda.ARS,
        dia_cierre=24,
        dia_vencimiento=7,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta_otro)

    cat = Categoria(id=uuid4(), nombre="Supermercado", tipo=TipoCategoria.EGRESO)
    db.add(cat)
    subcat = Subcategoria(id=uuid4(), categoria_id=cat.id, nombre="Alimentos", orden=1)
    db.add(subcat)

    db.commit()

    return {
        "usuario": usuario,
        "otro_usuario": otro_usuario,
        "billetera": billetera,
        "tarjeta": tarjeta,
        "tarjeta_otro": tarjeta_otro,
        "categoria": cat,
        "subcategoria": subcat,
    }


@pytest.fixture(name="client")
def client_fixture(db: Session, escenario: dict):
    def override_get_db():
        yield db

    def override_get_current_user():
        return escenario["usuario"]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()


def test_resumen_de_compra_caso_1(client: TestClient, escenario: dict):
    """1. Compra 2026-09-20 -> fecha_cierre 2026-09-24, fecha_vencimiento 2026-10-07."""
    tarjeta_id = str(escenario["tarjeta"].id)
    resp = client.get(f"/tarjetas/{tarjeta_id}/resumen-de-compra?fecha=2026-09-20")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fecha_cierre"] == "2026-09-24"
    assert data["fecha_vencimiento"] == "2026-10-07"


def test_resumen_de_compra_caso_2(client: TestClient, escenario: dict):
    """2. Compra 2026-09-26 -> fecha_cierre 2026-10-24, fecha_vencimiento 2026-11-09 (el 07/11 es sábado)."""
    tarjeta_id = str(escenario["tarjeta"].id)
    resp = client.get(f"/tarjetas/{tarjeta_id}/resumen-de-compra?fecha=2026-09-26")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fecha_cierre"] == "2026-10-24"
    assert data["fecha_vencimiento"] == "2026-11-09"


def test_resumen_de_compra_caso_3(client: TestClient, escenario: dict):
    """3. Compra 2026-09-20 con proximo_resumen=true -> fecha_cierre 2026-10-24, fecha_vencimiento 2026-11-09."""
    tarjeta_id = str(escenario["tarjeta"].id)
    resp = client.get(f"/tarjetas/{tarjeta_id}/resumen-de-compra?fecha=2026-09-20&proximo_resumen=true")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fecha_cierre"] == "2026-10-24"
    assert data["fecha_vencimiento"] == "2026-11-09"


def test_resumen_de_compra_caso_4(client: TestClient, escenario: dict):
    """4. Compra el mismo día del cierre, 2026-09-24 -> fecha_cierre 2026-09-24, fecha_vencimiento 2026-10-07."""
    tarjeta_id = str(escenario["tarjeta"].id)
    resp = client.get(f"/tarjetas/{tarjeta_id}/resumen-de-compra?fecha=2026-09-24")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fecha_cierre"] == "2026-09-24"
    assert data["fecha_vencimiento"] == "2026-10-07"


def test_resumen_de_compra_caso_5(client: TestClient, escenario: dict):
    """5. Tarjeta de otro usuario -> 404."""
    tarjeta_otro_id = str(escenario["tarjeta_otro"].id)
    resp = client.get(f"/tarjetas/{tarjeta_otro_id}/resumen-de-compra?fecha=2026-09-20")
    assert resp.status_code == 404
    data = resp.json()
    assert data["error"]["message"] == "No encontramos esa tarjeta."


def test_resumen_de_compra_caso_6(client: TestClient, db: Session, escenario: dict):
    """6. Crear con transaccion_service.crear_transaccion una compra a crédito en 1 cuota con fecha 2026-09-26:
    la fecha de vencimiento de su cuota es igual a la fecha_vencimiento que da el endpoint para esa fecha (2026-11-09)."""
    tarjeta = escenario["tarjeta"]
    billetera = escenario["billetera"]
    usuario = escenario["usuario"]
    cat = escenario["categoria"]
    subcat = escenario["subcategoria"]

    # Consultar endpoint para la fecha
    resp = client.get(f"/tarjetas/{tarjeta.id}/resumen-de-compra?fecha=2026-09-26")
    assert resp.status_code == 200
    endpoint_vencimiento = resp.json()["fecha_vencimiento"]
    assert endpoint_vencimiento == "2026-11-09"

    # Crear compra a crédito en 1 cuota
    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("15000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 26),
        descripcion="Compra prueba cuota",
        categoria_id=cat.id,
        subcategoria_id=subcat.id,
        metodo_pago=MetodoPago.CREDITO,
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        info_cuotas=InfoCuotas(
            cantidad_cuotas=1,
            cuota_inicial=1,
            tiene_interes=False,
            monto_total=Decimal("15000.00"),
            proximo_resumen=False,
        ),
    )
    tx = transaccion_service.crear_transaccion(db, usuario.id, tx_data, commit=True)
    assert tx is not None

    # Obtener cuota generada
    grupo = db.query(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx.id).first()
    assert grupo is not None
    cuota = db.query(Cuota).filter(Cuota.grupo_id == grupo.id).first()
    assert cuota is not None
    assert str(cuota.fecha_vencimiento) == endpoint_vencimiento
    assert cuota.fecha_vencimiento == date(2026, 11, 9)
