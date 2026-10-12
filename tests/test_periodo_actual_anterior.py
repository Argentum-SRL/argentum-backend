"""
Pruebas de ciclo actual y ciclo anterior en GET /dashboard/periodo-actual.
tests/test_periodo_actual_anterior.py
"""
from datetime import date
from unittest.mock import patch
from uuid import uuid4
import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Override de tipo JSONB para compatibilidad con SQLite en tests
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, RolUsuario, Usuario

# Configuración de base de datos SQLite en memoria
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

current_test_user = None


def override_get_current_user():
    if current_test_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )
    return current_test_user


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="client", scope="function")
def client_fixture(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)
    yield client

    app.dependency_overrides.clear()


def test_periodo_actual_anterior_dia_fijo_28_antes_cierre(client, db_session):
    """
    1. Usuario con ciclo DIA_FIJO día 28, hoy 2026-10-09:
    fecha_inicio: 2026-09-28, fecha_fin: 2026-10-27,
    ciclo_anterior_inicio: 2026-08-28, ciclo_anterior_fin: 2026-09-27.
    """
    global current_test_user
    user = Usuario(
        id=uuid4(),
        email="test_dia_fijo@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="28",
        ciclo_ajuste_direccion=None,
    )
    db_session.add(user)
    db_session.commit()
    current_test_user = user

    with patch("app.utils.fecha.hoy_argentina", return_value=date(2026, 10, 9)):
        response = client.get("/dashboard/periodo-actual")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["fecha_inicio"] == "2026-09-28"
        assert data["fecha_fin"] == "2026-10-27"
        assert data["ciclo_anterior_inicio"] == "2026-08-28"
        assert data["ciclo_anterior_fin"] == "2026-09-27"


def test_periodo_actual_anterior_dia_fijo_28_dia_inicio(client, db_session):
    """
    2. Mismo usuario con ciclo DIA_FIJO día 28, hoy 2026-10-28:
    fecha_inicio: 2026-10-28, fecha_fin: 2026-11-27,
    ciclo_anterior_inicio: 2026-09-28, ciclo_anterior_fin: 2026-10-27.
    """
    global current_test_user
    user = Usuario(
        id=uuid4(),
        email="test_dia_fijo_inicio@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="28",
        ciclo_ajuste_direccion=None,
    )
    db_session.add(user)
    db_session.commit()
    current_test_user = user

    with patch("app.utils.fecha.hoy_argentina", return_value=date(2026, 10, 28)):
        response = client.get("/dashboard/periodo-actual")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["fecha_inicio"] == "2026-10-28"
        assert data["fecha_fin"] == "2026-11-27"
        assert data["ciclo_anterior_inicio"] == "2026-09-28"
        assert data["ciclo_anterior_fin"] == "2026-10-27"


def test_periodo_actual_anterior_sin_ciclo_configurado(client, db_session):
    """
    3. Usuario sin ciclo configurado, hoy 2026-10-09:
    fecha_inicio: 2026-10-01, fecha_fin: 2026-10-31,
    ciclo_anterior_inicio: 2026-09-01, ciclo_anterior_fin: 2026-09-30.
    """
    global current_test_user
    user = Usuario(
        id=uuid4(),
        email="test_sin_ciclo@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=None,
        ciclo_valor=None,
        ciclo_ajuste_direccion=None,
    )
    db_session.add(user)
    db_session.commit()
    current_test_user = user

    with patch("app.utils.fecha.hoy_argentina", return_value=date(2026, 10, 9)):
        response = client.get("/dashboard/periodo-actual")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["fecha_inicio"] == "2026-10-01"
        assert data["fecha_fin"] == "2026-10-31"
        assert data["ciclo_anterior_inicio"] == "2026-09-01"
        assert data["ciclo_anterior_fin"] == "2026-09-30"
