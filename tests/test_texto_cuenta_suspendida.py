"""Pruebas del texto exacto de cuenta suspendida en login (DE.2.4)."""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

if not hasattr(JSONB, "_compiler_dispatcher") or (
    "sqlite" not in JSONB._compiler_dispatcher.specs
):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"


if not hasattr(PGUUID, "_compiler_dispatcher") or (
    "sqlite" not in PGUUID._compiler_dispatcher.specs
):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"


from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.core.reglas_texto_web import problemas_texto_web
from app.core.security import get_password_hash
from app.main import app
from app.models.usuario import AuthProvider, EstadoUsuario, RolUsuario, Usuario

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine,
)


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    """Desactiva el rate limiter y los envíos reales de email."""
    monkeypatch.setattr(limiter, "enabled", False)
    mock_notif = MagicMock(return_value=True)
    mock_email = MagicMock(return_value=True)
    monkeypatch.setattr(
        "app.services.notificacion_email_service.enviar_email_notificacion",
        mock_notif,
    )
    monkeypatch.setattr("app.services.email_service._enviar_email", mock_email)
    monkeypatch.setattr("app.services.email_service.SessionLocal", TestingSessionLocal)


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    """Crea un esquema limpio en SQLite por cada prueba."""
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(name="client", scope="function")
def client_fixture(db_session):
    """Cliente FastAPI conectado a la sesión SQLite de prueba."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db_session):
    """Usuario de prueba verificado con email y contraseña."""
    usuario = Usuario(
        id=uuid4(),
        email="suspendido@argentum.app",
        nombre="Usuario",
        apellido="Suspendido",
        password_hash=get_password_hash("Clave1234"),
        password_configurada=True,
        email_verificado=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.ACTIVO,
        rol=RolUsuario.USUARIO,
    )
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    return usuario


def test_login_cuenta_suspendida_muestra_mail_contacto(
    client,
    db_session,
    usuario_test,
):
    """POST /auth/login con cuenta INACTIVO devuelve 403 y el texto literal exacto."""
    usuario_test.estado = EstadoUsuario.INACTIVO
    db_session.commit()

    res = client.post(
        "/auth/login",
        json={
            "email": "suspendido@argentum.app",
            "password": "Clave1234",
        },
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
    data = res.json()
    texto_esperado = (
        "Tu cuenta está suspendida. Si creés que es un error, "
        "escribí a srlargentum@gmail.com."
    )
    assert data["error"]["message"] == texto_esperado
    assert problemas_texto_web(data["error"]["message"]) == []
