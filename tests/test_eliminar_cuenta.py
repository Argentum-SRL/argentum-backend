"""
Pruebas unitarias para eliminacion de cuenta: tests/test_eliminar_cuenta.py
Verifica:
1. Usuario con contraseña correcta -> 200 y mock eliminar_usuario llamado 1 vez.
2. Contraseña incorrecta -> 400 ("La contraseña no es correcta.") y mock 0 llamadas.
3. Sin contraseña en payload -> 400 ("Ingresá tu contraseña para eliminar la cuenta.") y mock 0 llamadas.
4. Usuario Google sin contraseña + token valido (email coincidente normalizado) -> 200 y mock 1 llamada.
5. Usuario Google con email distinto devuelto por Google -> 400 ("La cuenta de Google no coincide con la tuya.") y mock 0 llamadas.
6. Usuario Google sin token en payload -> 400 ("Elegí tu cuenta de Google para eliminar la cuenta.") y mock 0 llamadas.
7. En app.routes no existe DELETE /usuarios/me y existe POST /usuarios/me/eliminar.
"""
from uuid import uuid4
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Compilar JSONB como TEXT para SQLite en memoria
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.main import app
from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.core.security import get_password_hash
from app.core.auth import crear_access_token
from app.models.usuario import Usuario, EstadoUsuario, AuthProvider, RolUsuario


test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def deshabilitar_rate_limiter(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
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
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(name="usuario_con_password")
def usuario_con_password_fixture(db_session):
    usuario = Usuario(
        id=uuid4(),
        email="usuario_pwd@argentum.app",
        nombre="Usuario",
        apellido="Password",
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


@pytest.fixture(name="usuario_google_sin_password")
def usuario_google_sin_password_fixture(db_session):
    usuario = Usuario(
        id=uuid4(),
        email="usuario_google@argentum.app",
        nombre="Usuario",
        apellido="Google",
        password_hash=None,
        password_configurada=False,
        email_verificado=True,
        auth_provider=AuthProvider.GOOGLE,
        estado=EstadoUsuario.ACTIVO,
        rol=RolUsuario.USUARIO,
    )
    db_session.add(usuario)
    db_session.commit()
    db_session.refresh(usuario)
    return usuario


def _get_error_message(res) -> str:
    data = res.json()
    if isinstance(data, dict):
        if "error" in data and isinstance(data["error"], dict):
            return data["error"].get("message", "")
        return data.get("detail", "")
    return str(data)


# ── Caso 1: Usuario con contraseña correcta -> 200 y mock llamado 1 vez ─────────
def test_01_eliminar_cuenta_password_correcta(client, db_session, usuario_con_password, monkeypatch):
    mock_eliminar = MagicMock(return_value={"confirmacion": "Usuario y todos sus datos eliminados correctamente"})
    monkeypatch.setattr("app.services.usuario_service.eliminar_usuario", mock_eliminar)

    token = crear_access_token(usuario_con_password.id)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/usuarios/me/eliminar", json={"password": "Clave1234"}, headers=headers)
    assert res.status_code == status.HTTP_200_OK
    assert mock_eliminar.call_count == 1


# ── Caso 2: Mismo usuario + contraseña incorrecta -> 400 y mock 0 llamadas ─────
def test_02_eliminar_cuenta_password_incorrecta(client, db_session, usuario_con_password, monkeypatch):
    mock_eliminar = MagicMock()
    monkeypatch.setattr("app.services.usuario_service.eliminar_usuario", mock_eliminar)

    token = crear_access_token(usuario_con_password.id)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/usuarios/me/eliminar", json={"password": "Otra1234"}, headers=headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert _get_error_message(res) == "La contraseña no es correcta."
    assert mock_eliminar.call_count == 0


# ── Caso 3: Mismo usuario + payload vacio -> 400 y mock 0 llamadas ─────────────
def test_03_eliminar_cuenta_password_vacia(client, db_session, usuario_con_password, monkeypatch):
    mock_eliminar = MagicMock()
    monkeypatch.setattr("app.services.usuario_service.eliminar_usuario", mock_eliminar)

    token = crear_access_token(usuario_con_password.id)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/usuarios/me/eliminar", json={}, headers=headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert _get_error_message(res) == "Ingresá tu contraseña para eliminar la cuenta."
    assert mock_eliminar.call_count == 0


# ── Caso 4: Usuario Google sin contraseña + token valido -> 200 y mock 1 llamada
def test_04_eliminar_cuenta_google_valido(client, db_session, usuario_google_sin_password, monkeypatch):
    mock_eliminar = MagicMock(return_value={"confirmacion": "Usuario y todos sus datos eliminados correctamente"})
    monkeypatch.setattr("app.services.usuario_service.eliminar_usuario", mock_eliminar)

    # Mock verify_google_token devolviendo el email en MAYUSCULAS y email_verified=True
    monkeypatch.setattr(
        "app.services.usuario_service.verify_google_token",
        lambda token: {
            "email": usuario_google_sin_password.email.upper(),
            "email_verified": True,
        }
    )

    token = crear_access_token(usuario_google_sin_password.id)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/usuarios/me/eliminar", json={"google_token": "x"}, headers=headers)
    assert res.status_code == status.HTTP_200_OK
    assert mock_eliminar.call_count == 1


# ── Caso 5: Mismo usuario Google, Google devuelve otro email -> 400 y mock 0 llamadas
def test_05_eliminar_cuenta_google_otro_email(client, db_session, usuario_google_sin_password, monkeypatch):
    mock_eliminar = MagicMock()
    monkeypatch.setattr("app.services.usuario_service.eliminar_usuario", mock_eliminar)

    monkeypatch.setattr(
        "app.services.usuario_service.verify_google_token",
        lambda token: {
            "email": "otro_usuario@gmail.com",
            "email_verified": True,
        }
    )

    token = crear_access_token(usuario_google_sin_password.id)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/usuarios/me/eliminar", json={"google_token": "x"}, headers=headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert _get_error_message(res) == "La cuenta de Google no coincide con la tuya."
    assert mock_eliminar.call_count == 0


# ── Caso 6: Mismo usuario Google + payload vacio -> 400 y mock 0 llamadas ──────
def test_06_eliminar_cuenta_google_sin_token(client, db_session, usuario_google_sin_password, monkeypatch):
    mock_eliminar = MagicMock()
    monkeypatch.setattr("app.services.usuario_service.eliminar_usuario", mock_eliminar)

    token = crear_access_token(usuario_google_sin_password.id)
    headers = {"Authorization": f"Bearer {token}"}

    res = client.post("/usuarios/me/eliminar", json={}, headers=headers)
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert _get_error_message(res) == "Elegí tu cuenta de Google para eliminar la cuenta."
    assert mock_eliminar.call_count == 0


# ── Caso 7: Control de rutas en app.routes ──────────────────────────────────────
def test_07_rutas_eliminar_cuenta():
    # En app.routes no hay ninguna ruta "/usuarios/me" con método DELETE
    rutas_delete_me = [
        r for r in app.routes
        if getattr(r, "path", None) == "/usuarios/me" and "DELETE" in getattr(r, "methods", set())
    ]
    assert len(rutas_delete_me) == 0, "No debe existir ninguna ruta DELETE /usuarios/me"

    # "/usuarios/me/eliminar" tiene POST
    rutas_post_eliminar = [
        r for r in app.routes
        if getattr(r, "path", None) == "/usuarios/me/eliminar" and "POST" in getattr(r, "methods", set())
    ]
    assert len(rutas_post_eliminar) == 1, "Debe existir exactamente una ruta POST /usuarios/me/eliminar"
