# -*- coding: utf-8 -*-
"""
tests/test_j2_registro_login.py — Pruebas para registro y login sin revelar cuentas (fase_j2_auth).
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Compilar JSONB como TEXT para SQLite en memoria
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.core.security import get_password_hash, verify_password
from app.main import app
from app.models.usuario import AuthProvider, EstadoUsuario, Usuario
from app.services import email_service
from app.services.email_service import (
    enviar_email_cuenta_existente,
    enviar_email_recuperacion,
    generar_y_enviar_verificacion_email,
)
import app.routers.auth as auth_router

BODY_REGISTRO = {
    "access_token": None,
    "token_type": "bearer",
    "usuario": None,
    "requiere_telefono": False,
    "requiere_datos": False,
    "requiere_verificacion_email": True,
    "requiere_verificacion_telefono": False,
    "requiere_onboarding": False,
}

BODY_401 = {
    "success": False,
    "error": {
        "code": "UNAUTHORIZED",
        "message": "El email o la contraseña no son correctos. Revisalos e intentá de nuevo.",
    },
}

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_entorno(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    mock_notif = MagicMock(return_value=True)
    mock_email = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.notificacion_email_service.enviar_email_notificacion", mock_notif)
    monkeypatch.setattr("app.services.email_service._enviar_email", mock_email)
    monkeypatch.setattr("app.services.email_service.SessionLocal", TestingSessionLocal)


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


@pytest.fixture(name="auth_mocks")
def auth_mocks_fixture(monkeypatch):
    calls_generar: list[tuple] = []
    calls_cuenta_existente: list[tuple] = []
    hash_calls = [0]
    verify_calls = [0]

    orig_hash = auth_router.get_password_hash
    orig_verify = auth_router.verify_password

    def wrapped_hash(*args, **kwargs):
        hash_calls[0] += 1
        return orig_hash(*args, **kwargs)

    def wrapped_verify(*args, **kwargs):
        verify_calls[0] += 1
        return orig_verify(*args, **kwargs)

    def mock_generar(*args, **kwargs):
        calls_generar.append((args, kwargs))
        return "123456"

    def mock_cuenta_existente(*args, **kwargs):
        calls_cuenta_existente.append((args, kwargs))
        return True

    monkeypatch.setattr(auth_router, "verificar_turnstile_token", lambda tok, ip: True)
    monkeypatch.setattr(auth_router, "generar_y_enviar_verificacion_email", mock_generar)
    monkeypatch.setattr(auth_router, "enviar_email_cuenta_existente", mock_cuenta_existente)
    monkeypatch.setattr(auth_router, "get_password_hash", wrapped_hash)
    monkeypatch.setattr(auth_router, "verify_password", wrapped_verify)

    return {
        "generar": calls_generar,
        "cuenta_existente": calls_cuenta_existente,
        "hash_calls": hash_calls,
        "verify_calls": verify_calls,
    }


# ---------------------------------------------------------------------------
# Tests R1 - R8 (Registro)
# ---------------------------------------------------------------------------

def test_r1_registro_email_nuevo(client, db_session, auth_mocks):
    payload = {
        "nombre": "Ana",
        "apellido": "Perez",
        "email": "nuevo@prueba.com",
        "password": "Password123",
        "turnstile_token": "token-ok",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 201
    assert res.json() == BODY_REGISTRO

    user = db_session.execute(select(Usuario).where(Usuario.email == "nuevo@prueba.com")).scalar_one_or_none()
    assert user is not None
    assert user.estado == EstadoUsuario.PENDIENTE_VERIFICACION
    assert user.email_verificado is False

    assert len(auth_mocks["generar"]) == 1
    args, kwargs = auth_mocks["generar"][0]
    assert args == ("nuevo@prueba.com",)
    assert kwargs.get("nombre") == "Ana"
    assert len(auth_mocks["cuenta_existente"]) == 0
    assert auth_mocks["hash_calls"][0] == 1


def test_r2_registro_cuenta_email_verificada_y_activa(client, db_session, auth_mocks):
    user = Usuario(
        id=uuid4(),
        nombre="Beto",
        apellido="Lopez",
        email="existe@prueba.com",
        password_hash=get_password_hash("ClaveVieja123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()
    auth_mocks["hash_calls"][0] = 0

    count_antes = db_session.execute(select(Usuario)).scalars().all()

    payload = {
        "nombre": "Roberto",
        "apellido": "Gomez",
        "email": "existe@prueba.com",
        "password": "NuevaPassword456",
        "turnstile_token": "token-ok",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 201
    assert res.json() == BODY_REGISTRO

    assert len(auth_mocks["cuenta_existente"]) == 1
    args, kwargs = auth_mocks["cuenta_existente"][0]
    assert args == ("existe@prueba.com",)
    assert kwargs.get("nombre") == "Beto"
    assert kwargs.get("con_google") is False
    assert len(auth_mocks["generar"]) == 0
    assert auth_mocks["hash_calls"][0] == 1

    count_despues = db_session.execute(select(Usuario)).scalars().all()
    assert len(count_antes) == len(count_despues)

    db_session.refresh(user)
    assert verify_password("ClaveVieja123", user.password_hash) is True


def test_r3_registro_cuenta_google_verificada(client, db_session, auth_mocks):
    user = Usuario(
        id=uuid4(),
        nombre="Caro",
        apellido="Diaz",
        email="google@prueba.com",
        password_hash=None,
        password_configurada=False,
        auth_provider=AuthProvider.GOOGLE,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()

    payload = {
        "nombre": "Carolina",
        "apellido": "Diaz",
        "email": "google@prueba.com",
        "password": "Password789",
        "turnstile_token": "token-ok",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 201
    assert res.json() == BODY_REGISTRO

    assert len(auth_mocks["cuenta_existente"]) == 1
    args, kwargs = auth_mocks["cuenta_existente"][0]
    assert kwargs.get("con_google") is True

    db_session.refresh(user)
    assert user.password_hash is None


def test_r4_registro_cuenta_email_pendiente(client, db_session, auth_mocks):
    user = Usuario(
        id=uuid4(),
        nombre="Dani",
        apellido="Perez",
        email="pend@prueba.com",
        password_hash=get_password_hash("ClaveVieja123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        email_verificado=False,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()
    auth_mocks["hash_calls"][0] = 0

    count_antes = db_session.execute(select(Usuario)).scalars().all()

    payload = {
        "nombre": "Daniela",
        "apellido": "Gomez",
        "email": "pend@prueba.com",
        "password": "ClaveNueva456",
        "turnstile_token": "token-ok",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 201
    assert res.json() == BODY_REGISTRO

    db_session.refresh(user)
    assert user.nombre == "Daniela"
    assert user.apellido == "Gomez"
    assert verify_password("ClaveNueva456", user.password_hash) is True
    assert verify_password("ClaveVieja123", user.password_hash) is False

    assert len(auth_mocks["generar"]) == 1
    args, kwargs = auth_mocks["generar"][0]
    assert args == ("pend@prueba.com",)
    assert kwargs.get("nombre") == "Daniela"
    assert len(auth_mocks["cuenta_existente"]) == 0

    count_despues = db_session.execute(select(Usuario)).scalars().all()
    assert len(count_antes) == len(count_despues)


def test_r5_registro_cuenta_email_verificada_inactivo(client, db_session, auth_mocks):
    user = Usuario(
        id=uuid4(),
        nombre="Inactivo",
        apellido="Usuario",
        email="inactivo@prueba.com",
        password_hash=get_password_hash("Clave123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.INACTIVO,
        email_verificado=True,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()

    payload = {
        "nombre": "NuevoNombre",
        "apellido": "NuevoApellido",
        "email": "inactivo@prueba.com",
        "password": "Password999",
        "turnstile_token": "token-ok",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 201
    assert res.json() == BODY_REGISTRO

    assert len(auth_mocks["cuenta_existente"]) == 1
    assert auth_mocks["cuenta_existente"][0][1].get("con_google") is False

    db_session.refresh(user)
    assert user.estado == EstadoUsuario.INACTIVO


def test_r6_registro_telefono_ya_usado(client, db_session, auth_mocks):
    otra = Usuario(
        id=uuid4(),
        nombre="Existente",
        apellido="User",
        email="existente_tel@prueba.com",
        telefono="+5493415550000",
        telefono_normalizado="+5493415550000",
        password_hash=get_password_hash("Clave123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        moneda_principal="ARS",
    )
    db_session.add(otra)
    db_session.commit()

    payload = {
        "nombre": "Telefono",
        "apellido": "Prueba",
        "email": "tel@prueba.com",
        "telefono": "+5493415550000",
        "password": "Password123",
        "turnstile_token": "token-ok",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 201
    assert res.json() == BODY_REGISTRO

    nueva = db_session.execute(select(Usuario).where(Usuario.email == "tel@prueba.com")).scalar_one_or_none()
    assert nueva is not None
    assert nueva.telefono is None
    assert nueva.telefono_normalizado is None

    db_session.refresh(otra)
    assert otra.telefono == "+5493415550000"


def test_r7_captcha_invalido(client, monkeypatch):
    monkeypatch.setattr(auth_router, "verificar_turnstile_token", lambda tok, ip: False)
    payload = {
        "nombre": "Ana",
        "apellido": "Perez",
        "email": "captcha_fail@prueba.com",
        "password": "Password123",
        "turnstile_token": "token-invalido",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 400
    assert "La verificación de seguridad (captcha) falló o es inválida" in res.json()["error"]["message"]


def test_r8_registro_integrity_error(client, db_session, auth_mocks, monkeypatch):
    orig_commit = db_session.commit
    commit_llamadas = [0]

    def commit_con_error_primera_vez():
        commit_llamadas[0] += 1
        if commit_llamadas[0] == 1:
            raise IntegrityError("INSERT INTO usuarios...", {}, Exception("duplicate key value violates unique constraint"))
        return orig_commit()

    monkeypatch.setattr(db_session, "commit", commit_con_error_primera_vez)

    payload = {
        "nombre": "Simultaneo",
        "apellido": "Prueba",
        "email": "simultaneo@prueba.com",
        "password": "Password123",
        "turnstile_token": "token-ok",
    }
    res = client.post("/auth/register", json=payload)
    assert res.status_code == 201
    assert res.json() == BODY_REGISTRO
    assert len(auth_mocks["generar"]) == 0

    # Sesión utilizable tras rollback
    db_session.rollback()
    q = db_session.execute(select(Usuario)).all()
    assert isinstance(q, list)


# ---------------------------------------------------------------------------
# Tests L1 - L8 (Login)
# ---------------------------------------------------------------------------

def test_l1_login_email_inexistente(client, auth_mocks):
    res = client.post("/auth/login", json={"email": "inexistente@prueba.com", "password": "Password123"})
    assert res.status_code == 401
    assert res.json() == BODY_401
    assert auth_mocks["verify_calls"][0] == 1


def test_l2_login_cuenta_verificada_password_incorrecta(client, db_session):
    user = Usuario(
        id=uuid4(),
        nombre="Verificado",
        apellido="Test",
        email="verificado@prueba.com",
        password_hash=get_password_hash("PasswordCorrecta123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        intentos_fallidos_login=0,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()

    res = client.post("/auth/login", json={"email": "verificado@prueba.com", "password": "PasswordErronea"})
    assert res.status_code == 401
    assert res.json() == BODY_401

    db_session.refresh(user)
    assert user.intentos_fallidos_login == 1


def test_l3_login_cuenta_google_sin_password(client, db_session, auth_mocks):
    user = Usuario(
        id=uuid4(),
        nombre="GoogleUser",
        apellido="Test",
        email="google_user@prueba.com",
        password_hash=None,
        password_configurada=False,
        auth_provider=AuthProvider.GOOGLE,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()
    auth_mocks["verify_calls"][0] = 0

    res = client.post("/auth/login", json={"email": "google_user@prueba.com", "password": "CualquierPassword"})
    assert res.status_code == 401
    assert res.json() == BODY_401
    assert auth_mocks["verify_calls"][0] == 1


def test_l4_login_cuenta_no_verificada_password_incorrecta(client, db_session):
    user = Usuario(
        id=uuid4(),
        nombre="NoVerif",
        apellido="Test",
        email="noverif@prueba.com",
        password_hash=get_password_hash("PasswordCorrecta123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        email_verificado=False,
        intentos_fallidos_login=0,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()

    res = client.post("/auth/login", json={"email": "noverif@prueba.com", "password": "PasswordErronea"})
    assert res.status_code == 401
    assert res.json() == BODY_401

    db_session.refresh(user)
    assert user.intentos_fallidos_login == 1


def test_l5_login_cuenta_no_verificada_password_correcta(client, db_session):
    user = Usuario(
        id=uuid4(),
        nombre="NoVerif2",
        apellido="Test",
        email="noverif2@prueba.com",
        password_hash=get_password_hash("PasswordCorrecta123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        email_verificado=False,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()

    res = client.post("/auth/login", json={"email": "noverif2@prueba.com", "password": "PasswordCorrecta123"})
    assert res.status_code == 401
    data = res.json()
    assert data["error"]["code"] == "EMAIL_NO_VERIFICADO"
    assert data["error"]["message"] == "Todavía no verificaste tu cuenta. Revisá tu email para activarla."


def test_l6_login_bloqueo_cuenta_10_intentos(client, db_session):
    hace_5_min = datetime.now(timezone.utc) - timedelta(minutes=5)
    user = Usuario(
        id=uuid4(),
        nombre="Bloqueado",
        apellido="Test",
        email="bloqueado@prueba.com",
        password_hash=get_password_hash("PasswordCorrecta123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        intentos_fallidos_login=10,
        ultimo_intento_fallido_at=hace_5_min,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()

    # Contraseña incorrecta → 401 == BODY_401, intentos no suma
    res_bad = client.post("/auth/login", json={"email": "bloqueado@prueba.com", "password": "PasswordErronea"})
    assert res_bad.status_code == 401
    assert res_bad.json() == BODY_401

    db_session.refresh(user)
    assert user.intentos_fallidos_login == 10

    # Contraseña correcta → 429 con mensaje
    res_good = client.post("/auth/login", json={"email": "bloqueado@prueba.com", "password": "PasswordCorrecta123"})
    assert res_good.status_code == 429
    assert res_good.json()["error"]["message"] == "Hubo demasiados intentos fallidos con esta cuenta. Esperá una hora o recuperá tu contraseña."


def test_l7_login_cuenta_verificada_password_correcta(client, db_session):
    user = Usuario(
        id=uuid4(),
        nombre="Exito",
        apellido="Test",
        email="exito@prueba.com",
        password_hash=get_password_hash("PasswordCorrecta123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        intentos_fallidos_login=2,
        moneda_principal="ARS",
    )
    db_session.add(user)
    db_session.commit()

    res = client.post("/auth/login", json={"email": "exito@prueba.com", "password": "PasswordCorrecta123"})
    assert res.status_code == 200
    assert "access_token" in res.cookies
    assert "refresh_token" in res.cookies

    db_session.refresh(user)
    assert user.intentos_fallidos_login == 0


def test_l8_login_respuestas_identicas(client, db_session):
    u_verif = Usuario(
        id=uuid4(),
        nombre="V",
        apellido="T",
        email="u_verif@prueba.com",
        password_hash=get_password_hash("Pass123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        moneda_principal="ARS",
    )
    u_google = Usuario(
        id=uuid4(),
        nombre="G",
        apellido="T",
        email="u_google@prueba.com",
        password_hash=None,
        password_configurada=False,
        auth_provider=AuthProvider.GOOGLE,
        estado=EstadoUsuario.ACTIVO,
        email_verificado=True,
        moneda_principal="ARS",
    )
    u_noverif = Usuario(
        id=uuid4(),
        nombre="NV",
        apellido="T",
        email="u_noverif@prueba.com",
        password_hash=get_password_hash("Pass123"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        email_verificado=False,
        moneda_principal="ARS",
    )
    db_session.add_all([u_verif, u_google, u_noverif])
    db_session.commit()

    res1 = client.post("/auth/login", json={"email": "nadie@prueba.com", "password": "P"})
    res2 = client.post("/auth/login", json={"email": "u_verif@prueba.com", "password": "Wrong"})
    res3 = client.post("/auth/login", json={"email": "u_google@prueba.com", "password": "P"})
    res4 = client.post("/auth/login", json={"email": "u_noverif@prueba.com", "password": "Wrong"})

    assert res1.status_code == res2.status_code == res3.status_code == res4.status_code == 401
    assert res1.json() == res2.json() == res3.json() == res4.json() == BODY_401


# ---------------------------------------------------------------------------
# Tests M1 - M4 (Emails y links)
# ---------------------------------------------------------------------------

def test_m1_email_cuenta_existente_con_google_false(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://miargentum.com")
    enviados: list[dict] = []

    def mock_enviar(dest, asunto, cuerpo, cuerpo_html=None):
        enviados.append({
            "dest": dest,
            "asunto": asunto,
            "cuerpo": cuerpo,
            "html": cuerpo_html,
        })
        return True

    monkeypatch.setattr(email_service, "_enviar_email", mock_enviar)

    ret = enviar_email_cuenta_existente("existe@prueba.com", nombre="Beto", con_google=False)
    assert ret is True
    assert len(enviados) == 1
    m = enviados[0]
    assert m["asunto"] == "Ya tenés una cuenta en Argentum"
    esperado_cuerpo = (
        "Hola Beto:\n\n"
        "Alguien quiso crear una cuenta en Argentum con este mail, pero ya tenés una.\n\n"
        "Entrá acá: https://miargentum.com/login\n"
        "Si no te acordás la contraseña, recuperala acá: https://miargentum.com/auth/recuperar-password\n\n"
        "Si no fuiste vos, no tenés que hacer nada: tu cuenta sigue igual."
    )
    assert m["cuerpo"] == esperado_cuerpo
    assert "https://miargentum.com/login" in m["html"]
    assert "https://miargentum.com/auth/recuperar-password" in m["html"]
    assert "Entrar a Argentum" in m["html"]
    assert "Recuperar mi contraseña" in m["html"]


def test_m2_email_cuenta_existente_con_google_true(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://miargentum.com")
    enviados: list[dict] = []

    def mock_enviar(dest, asunto, cuerpo, cuerpo_html=None):
        enviados.append({
            "dest": dest,
            "asunto": asunto,
            "cuerpo": cuerpo,
            "html": cuerpo_html,
        })
        return True

    monkeypatch.setattr(email_service, "_enviar_email", mock_enviar)

    ret = enviar_email_cuenta_existente("existe@prueba.com", nombre="Beto", con_google=True)
    assert ret is True
    assert len(enviados) == 1
    m = enviados[0]
    esperado_cuerpo = (
        "Hola Beto:\n\n"
        "Alguien quiso crear una cuenta en Argentum con este mail, pero ya tenés una.\n\n"
        "Entrá con el botón de Google acá: https://miargentum.com/login\n"
        "Si preferís entrar con contraseña, creala acá: https://miargentum.com/auth/recuperar-password\n\n"
        "Si no fuiste vos, no tenés que hacer nada: tu cuenta sigue igual."
    )
    assert m["cuerpo"] == esperado_cuerpo
    assert "Entrar con Google" in m["html"]
    assert "Crear una contraseña" in m["html"]


def test_m3_email_cuenta_existente_sin_nombre(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://miargentum.com")
    enviados: list[dict] = []

    def mock_enviar(dest, asunto, cuerpo, cuerpo_html=None):
        enviados.append({"cuerpo": cuerpo})
        return True

    monkeypatch.setattr(email_service, "_enviar_email", mock_enviar)

    enviar_email_cuenta_existente("existe@prueba.com", nombre=None, con_google=False)
    assert len(enviados) == 1
    assert enviados[0]["cuerpo"].startswith("Hola:\n\nAlguien quiso")


def test_m4_links_con_urlencode(monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_URL", "https://api.miargentum.com")
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://miargentum.com")

    monkeypatch.setattr(email_service, "_generar_codigo", lambda: "123456")
    monkeypatch.setattr(email_service, "guardar_codigo_verificacion_email", lambda em, cod: None)

    enviados: list[dict] = []

    def mock_enviar(dest, asunto, cuerpo, cuerpo_html=None):
        enviados.append({"cuerpo": cuerpo, "html": cuerpo_html})
        return True

    monkeypatch.setattr(email_service, "_enviar_email", mock_enviar)

    generar_y_enviar_verificacion_email("ana+1@prueba.com", nombre="Ana")
    assert len(enviados) == 1
    assert "https://api.miargentum.com/auth/email/verificar-link?email=ana%2B1%40prueba.com&codigo=123456" in enviados[0]["cuerpo"]

    enviados.clear()
    enviar_email_recuperacion("ana+1@prueba.com", "654321")
    assert len(enviados) == 1
    contenido_recupero = (enviados[0]["cuerpo"] or "") + (enviados[0]["html"] or "")
    assert "https://miargentum.com/auth/recuperar-password?email=ana%2B1%40prueba.com&codigo=654321" in contenido_recupero


def test_estatico_textos_register():
    src = inspect.getsource(auth_router.register)
    assert "Ya existe una cuenta" not in src
    assert "registrado con Google" not in src
    assert "ya está registrado" not in src
