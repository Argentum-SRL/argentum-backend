# -*- coding: utf-8 -*-
import inspect
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

sqlite3.register_adapter(UUID, lambda u: u.hex)

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"

from app.main import app
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.limiter import limiter
from app.core.auth import crear_access_token
from app.models.usuario import Usuario, EstadoUsuario, AuthProvider
from app.services import auth_service
from app.services.auth_service import verify_google_token, TEXTO_GOOGLE_INVALIDO
from app.routers.notificaciones import autenticar_sse, sse_notificaciones


# Configuración de base de datos aislada en memoria
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    mock_email = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.email_service._enviar_email", mock_email)
    monkeypatch.setattr("app.services.notificacion_email_service.enviar_email_notificacion", mock_email)
    monkeypatch.setattr("app.services.email_service.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("app.routers.notificaciones.SessionLocal", TestingSessionLocal)


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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db_session):
    usuario = Usuario(
        id=UUID("22222222-2222-2222-2222-222222222222"),
        email="test_google@argentum.app",
        nombre="Usuario",
        apellido="Google",
        password_hash="hash_dummy",
        password_configurada=True,
        email_verificado=True,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.GOOGLE,
        moneda_principal="ARS",
    )
    db_session.add(usuario)
    db_session.commit()
    return usuario


# ---------------------------------------------------------------------------
# Fixture de claves RSA para Google ID Token
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk_dict["kid"] = "k1"
    jwk_dict["alg"] = "RS256"
    jwk_dict["use"] = "sig"

    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    return {
        "private_key": private_key,
        "public_jwk": jwk_dict,
        "jwks": {"keys": [jwk_dict]},
        "other_private_key": other_private_key,
    }


# ---------------------------------------------------------------------------
# Tests G1 - G12 (Google Auth)
# ---------------------------------------------------------------------------

def test_g1_google_token_valido(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_get_google_jwks", lambda forzar=False: rsa_keys["jwks"])

    llamadas_httpx = []
    def fake_httpx_get(url, *args, **kwargs):
        llamadas_httpx.append(str(url))
        assert "tokeninfo" not in str(url)
        raise AssertionError("httpx.get no debería llamarse")
    monkeypatch.setattr(auth_service.httpx, "get", fake_httpx_get)

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    tok = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})
    data = verify_google_token(tok)
    assert data["email"] == "g@prueba.com"

    # También con iss "accounts.google.com"
    payload["iss"] = "accounts.google.com"
    tok2 = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})
    data2 = verify_google_token(tok2)
    assert data2["email"] == "g@prueba.com"
    assert len(llamadas_httpx) == 0


def test_g2_google_aud_erronea(monkeypatch, rsa_keys, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_get_google_jwks", lambda forzar=False: rsa_keys["jwks"])

    llamadas_httpx = []
    monkeypatch.setattr(auth_service.httpx, "get", lambda url, *a, **kw: llamadas_httpx.append(str(url)))

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "otro-cliente",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    tok = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})

    caplog.clear()
    with pytest.raises(HTTPException) as exc_info:
        verify_google_token(tok)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == TEXTO_GOOGLE_INVALIDO
    assert len(llamadas_httpx) == 0

    # G10: caplog de G2 no contiene el token ni "g@prueba.com"
    assert tok not in caplog.text
    assert "g@prueba.com" not in caplog.text


def test_g3_google_iss_erroneo(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_get_google_jwks", lambda forzar=False: rsa_keys["jwks"])

    now_ts = int(time.time())
    payload = {
        "iss": "https://evil.example",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    tok = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(HTTPException) as exc_info:
        verify_google_token(tok)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == TEXTO_GOOGLE_INVALIDO


def test_g4_google_vencido(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_get_google_jwks", lambda forzar=False: rsa_keys["jwks"])

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts - 7200,
        "exp": now_ts - 3600,
    }
    tok = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(HTTPException) as exc_info:
        verify_google_token(tok)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == TEXTO_GOOGLE_INVALIDO


def test_g5_google_otra_clave_mismo_kid(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_get_google_jwks", lambda forzar=False: rsa_keys["jwks"])

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    tok = jwt.encode(payload, rsa_keys["other_private_key"], algorithm="RS256", headers={"kid": "k1"})
    with pytest.raises(HTTPException) as exc_info:
        verify_google_token(tok)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == TEXTO_GOOGLE_INVALIDO


def test_g6_google_hs256_o_sin_kid(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_get_google_jwks", lambda forzar=False: rsa_keys["jwks"])

    llamadas_httpx = []
    def fake_httpx_get(url, *args, **kwargs):
        llamadas_httpx.append(str(url))
        assert "tokeninfo" not in str(url)
    monkeypatch.setattr(auth_service.httpx, "get", fake_httpx_get)

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    # Token HS256 con kid "k1"
    tok_hs256 = jwt.encode(payload, "clave-simetrica", algorithm="HS256", headers={"kid": "k1"})
    with pytest.raises(HTTPException) as exc1:
        verify_google_token(tok_hs256)
    assert exc1.value.status_code == 400
    assert exc1.value.detail == TEXTO_GOOGLE_INVALIDO

    # Token sin kid
    tok_sin_kid = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256")
    with pytest.raises(HTTPException) as exc2:
        verify_google_token(tok_sin_kid)
    assert exc2.value.status_code == 400
    assert exc2.value.detail == TEXTO_GOOGLE_INVALIDO

    assert len(llamadas_httpx) == 0


def test_g7_rotacion_con_refetch(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_GOOGLE_JWKS", {"keys": []})
    monkeypatch.setattr(auth_service, "_JWKS_LAST_FETCH", time.time() - 600)

    llamadas_httpx = []
    class MockResponse:
        status_code = 200
        def json(self):
            return rsa_keys["jwks"]
        def raise_for_status(self):
            pass

    def fake_httpx_get(url, *args, **kwargs):
        llamadas_httpx.append(str(url))
        assert "tokeninfo" not in str(url)
        return MockResponse()

    monkeypatch.setattr(auth_service.httpx, "get", fake_httpx_get)

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    tok = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})

    data = verify_google_token(tok)
    assert data["email"] == "g@prueba.com"
    assert len(llamadas_httpx) == 1


def test_g8_rotacion_rechazada_antes_de_300s(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_GOOGLE_JWKS", {"keys": []})
    monkeypatch.setattr(auth_service, "_JWKS_LAST_FETCH", time.time() - 100)

    llamadas_httpx = []
    monkeypatch.setattr(auth_service.httpx, "get", lambda url, *a, **kw: llamadas_httpx.append(str(url)))

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    tok = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})

    with pytest.raises(HTTPException) as exc_info:
        verify_google_token(tok)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == TEXTO_GOOGLE_INVALIDO
    assert len(llamadas_httpx) == 0


def test_g9_rotacion_httpx_falla(monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_GOOGLE_JWKS", None)
    monkeypatch.setattr(auth_service, "_JWKS_LAST_FETCH", 0.0)

    def fake_httpx_raise(url, *args, **kwargs):
        raise httpx.RequestError("Conexión rechazada")
    monkeypatch.setattr(auth_service.httpx, "get", fake_httpx_raise)

    now_ts = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "cliente-prueba",
        "sub": "123",
        "email": "g@prueba.com",
        "email_verified": True,
        "iat": now_ts,
        "exp": now_ts + 3600,
    }
    tok = jwt.encode(payload, rsa_keys["private_key"], algorithm="RS256", headers={"kid": "k1"})

    with pytest.raises(HTTPException) as exc_info:
        verify_google_token(tok)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == TEXTO_GOOGLE_INVALIDO


def test_g11_estatico_sin_tokeninfo():
    auth_srv_path = Path(__file__).resolve().parent.parent / "app" / "services" / "auth_service.py"
    content = auth_srv_path.read_text(encoding="utf-8")
    assert "tokeninfo" not in content


def test_g12_endpoint_google_rechazo(client, monkeypatch, rsa_keys):
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "cliente-prueba")
    monkeypatch.setattr(auth_service, "_get_google_jwks", lambda forzar=False: rsa_keys["jwks"])

    res = client.post("/auth/google", json={"token": "token-invalido-que-no-valida"})
    assert res.status_code == 400
    assert res.json()["error"]["message"] == "No pudimos validar tu cuenta de Google. Probá de nuevo."


# ---------------------------------------------------------------------------
# Tests S1 - S7 (SSE Notificaciones)
# ---------------------------------------------------------------------------

def test_s1_sse_cookie_valida(db_session, usuario_test, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    token = crear_access_token(usuario_test.id)

    scope = {
        "type": "http",
        "headers": [(b"cookie", f"access_token={token}".encode("latin-1"))],
    }
    req = Request(scope=scope)
    uid, iat_dt = autenticar_sse(req)

    assert uid == usuario_test.id
    assert isinstance(iat_dt, datetime)
    assert iat_dt.tzinfo == timezone.utc


def test_s2_sse_sin_cookie_con_query(usuario_test, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    token = crear_access_token(usuario_test.id)

    scope = {
        "type": "http",
        "headers": [],
        "query_string": f"token={token}".encode("latin-1"),
    }
    req = Request(scope=scope)

    with pytest.raises(HTTPException) as exc_info:
        autenticar_sse(req)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token de acceso requerido"


def test_s3_sse_con_header_authorization(usuario_test, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    token = crear_access_token(usuario_test.id)

    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode("latin-1"))],
        "query_string": b"",
    }
    req = Request(scope=scope)

    with pytest.raises(HTTPException) as exc_info:
        autenticar_sse(req)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Token de acceso requerido"


def test_s4_sse_cookie_invalida_o_refresh(usuario_test, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")

    # 1. Token inválido
    scope_bad = {
        "type": "http",
        "headers": [(b"cookie", b"access_token=token-invalido")],
    }
    req_bad = Request(scope=scope_bad)
    with pytest.raises(HTTPException) as exc1:
        autenticar_sse(req_bad)
    assert exc1.value.status_code == 401
    assert exc1.value.detail == "Token de acceso inválido o expirado"

    # 2. Token type refresh
    now_ts = int(time.time())
    tok_refresh = jwt.encode(
        {"sub": str(usuario_test.id), "type": "refresh", "exp": now_ts + 3600, "iat": now_ts},
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    scope_refresh = {
        "type": "http",
        "headers": [(b"cookie", f"access_token={tok_refresh}".encode("latin-1"))],
    }
    req_refresh = Request(scope=scope_refresh)
    with pytest.raises(HTTPException) as exc2:
        autenticar_sse(req_refresh)
    assert exc2.value.status_code == 401
    assert exc2.value.detail == "Token de acceso inválido o expirado"


def test_s5_sse_usuario_inexistente_o_revocado(db_session, usuario_test, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")

    # 1. Usuario inexistente
    tok_inexistente = crear_access_token(uuid4())
    scope_inex = {
        "type": "http",
        "headers": [(b"cookie", f"access_token={tok_inexistente}".encode("latin-1"))],
    }
    req_inex = Request(scope=scope_inex)
    with pytest.raises(HTTPException) as exc1:
        autenticar_sse(req_inex)
    assert exc1.value.status_code == 401
    assert exc1.value.detail == "No encontramos una cuenta con esos datos."

    # 2. Sesión revocada
    usuario_test.tokens_revocados_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.commit()

    token_revocado = crear_access_token(usuario_test.id)
    scope_rev = {
        "type": "http",
        "headers": [(b"cookie", f"access_token={token_revocado}".encode("latin-1"))],
    }
    req_rev = Request(scope=scope_rev)
    with pytest.raises(HTTPException) as exc2:
        autenticar_sse(req_rev)
    assert exc2.value.status_code == 401
    assert exc2.value.detail == "Sesión revocada. Por favor iniciá sesión nuevamente."


def test_s6_sse_endpoint_get_sin_cookie(client, usuario_test, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    token = crear_access_token(usuario_test.id)

    res = client.get(f"/notificaciones/sse?token={token}")
    assert res.status_code == 401


def test_s7_estatico_sse():
    sig = inspect.signature(sse_notificaciones)
    assert "token" not in sig.parameters

    src = inspect.getsource(autenticar_sse)
    assert "query_params" not in src
    assert "request.headers" not in src
