"""
tests/pg/conftest.py — Configuración y fixtures para pruebas en PostgreSQL.

- Si DATABASE_URL no contiene "@localhost:5433/argentum_local", todos los tests de tests/pg se saltean.
- Si lo contiene, usa esa misma URL con la base argentum_pruebas_saldo (la crea si no existe conectándose a postgres con autocommit).
- Al empezar la sesión: DROP SCHEMA public CASCADE, CREATE SCHEMA public, Base.metadata.create_all.
- Nunca escribe en argentum_local.
- Ayuda fijar_hoy(monkeypatch, fecha).
- Fixtures A y B.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal
import uuid
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from app.models.billetera import Billetera, EstadoBilletera
from app.models.tarjeta_credito import TarjetaCredito, RedTarjeta, EstadoTarjeta
from app.models.usuario import Usuario, EstadoUsuario, RolUsuario, AuthProvider, Moneda


def _get_base_postgres_url() -> str:
    db_url = os.environ.get("DATABASE_URL", str(settings.DATABASE_URL))
    return db_url


def pytest_collection_modifyitems(config, items):
    db_url = _get_base_postgres_url()
    if "@localhost:5433/argentum_local" not in db_url:
        skip_pg = pytest.mark.skip(reason="solo con con_base_local.py")
        for item in items:
            if "tests/pg" in str(item.fspath).replace("\\", "/"):
                item.add_marker(skip_pg)


@pytest.fixture(scope="session", autouse=True)
def check_pg_env():
    db_url = _get_base_postgres_url()
    if "@localhost:5433/argentum_local" not in db_url:
        pytest.skip("solo con con_base_local.py", allow_module_level=True)


@pytest.fixture(scope="session")
def pg_engine():
    db_url = _get_base_postgres_url()
    if "@localhost:5433/argentum_local" not in db_url:
        pytest.skip("solo con con_base_local.py")

    # URL conectando a la base administrativa postgres para asegurar existencia de argentum_pruebas_saldo
    postgres_admin_url = db_url.replace("/argentum_local", "/postgres")
    admin_engine = create_engine(postgres_admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'argentum_pruebas_saldo'")
        ).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE argentum_pruebas_saldo"))
    admin_engine.dispose()

    # Engine para argentum_pruebas_saldo
    test_db_url = db_url.replace("/argentum_local", "/argentum_pruebas_saldo")
    engine = create_engine(test_db_url, pool_pre_ping=True)

    # Recrear esquema public limpio al inicio de la sesión
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    import app.models  # Cargar todos los modelos en Base.metadata
    _ = app.models
    from app.core.database import Base
    Base.metadata.create_all(bind=engine)

    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def SessionTesting(pg_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)


@pytest.fixture
def db(SessionTesting, monkeypatch):
    """Proporciona una sesión de BD para tests sobre argentum_pruebas_saldo y parchea SessionLocal."""
    session = SessionTesting()
    monkeypatch.setattr("app.core.database.SessionLocal", SessionTesting)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def fijar_hoy(monkeypatch, fecha: date):
    """
    Parchea app.utils.fecha.hoy_argentina, todo módulo ya importado de app que tenga
    el atributo hoy_argentina, y transaccion_service._hoy_argentina.
    """
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha)

    for name, mod in list(sys.modules.items()):
        if name.startswith("app.") and hasattr(mod, "hoy_argentina"):
            try:
                monkeypatch.setattr(mod, "hoy_argentina", lambda: fecha)
            except Exception:
                pass

    from app.services import transaccion_service
    if hasattr(transaccion_service, "_hoy_argentina"):
        monkeypatch.setattr(transaccion_service, "_hoy_argentina", lambda: fecha)


@pytest.fixture
def usuario_test(db: Session) -> Usuario:
    u = Usuario(
        id=uuid.uuid4(),
        email=f"test_{uuid.uuid4().hex[:8]}@example.com",
        nombre="Usuario Test PG",
        password_hash="fakehash",
        rol=RolUsuario.USUARIO,
        is_admin=False,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        auth_provider=AuthProvider.EMAIL,
        onboarding_completado=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def crear_fixture_a(db: Session, usuario_id: uuid.UUID, hoy: date, nombre: str = "Billetera A") -> Billetera:
    fecha_20 = hoy - timedelta(days=20)
    fc_dt = datetime.combine(fecha_20, time(12, 0)).replace(tzinfo=timezone.utc)
    b = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario_id,
        nombre=nombre,
        moneda=Moneda.ARS,
        saldo_actual=Decimal("270000.00"),
        saldo_inicial=Decimal("270000.00"),
        fecha_saldo=fecha_20,
        tiene_punto_partida=True,
        fecha_creacion=fc_dt,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def crear_fixture_b(db: Session, usuario_id: uuid.UUID, hoy: date, nombre: str = "Billetera B") -> Billetera:
    fecha_20 = hoy - timedelta(days=20)
    fc_dt = datetime.combine(fecha_20, time(12, 0)).replace(tzinfo=timezone.utc)
    b = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario_id,
        nombre=nombre,
        es_efectivo=True,
        moneda=Moneda.ARS,
        saldo_actual=Decimal("0.00"),
        saldo_inicial=Decimal("0.00"),
        fecha_saldo=None,
        tiene_punto_partida=False,
        fecha_creacion=fc_dt,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def crear_tarjeta_para_billetera(db: Session, usuario_id: uuid.UUID, billetera_id: uuid.UUID, nombre: str = "Visa Test") -> TarjetaCredito:
    t = TarjetaCredito(
        id=uuid.uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        nombre=nombre,
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=5,
        limite_credito=Decimal("500000.00"),
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


from app.models.categoria import Categoria, TipoCategoria, EstadoCategoria


def crear_categoria(db: Session, nombre: str = "General") -> Categoria:
    cat = Categoria(
        id=uuid.uuid4(),
        nombre=nombre,
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@pytest.fixture
def categoria_test(db: Session) -> Categoria:
    return crear_categoria(db, "General Test")


@pytest.fixture
def fixture_a(db: Session, usuario_test: Usuario) -> Billetera:
    from app.utils.fecha import hoy_argentina
    return crear_fixture_a(db, usuario_test.id, hoy_argentina())


@pytest.fixture
def fixture_b(db: Session, usuario_test: Usuario) -> Billetera:
    from app.utils.fecha import hoy_argentina
    return crear_fixture_b(db, usuario_test.id, hoy_argentina())
