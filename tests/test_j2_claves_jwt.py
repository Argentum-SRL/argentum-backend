# -*- coding: utf-8 -*-
import ast
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import jwt
import pytest
import sqlite3
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

sqlite3.register_adapter(UUID, lambda u: u.hex)

# Compilar JSONB y UUID para SQLite en memoria
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"

from app.core.config import settings
from app.core.database import Base
from app.core.limiter import limiter
from app.core.claves import (
    clave_hmac,
    claves_hmac_verificacion,
    avisar_si_falta_clave_hmac,
)
from app.core.auth import (
    crear_access_token,
    verificar_access_token,
    crear_refresh_token,
    renovar_tokens,
    get_current_user,
    get_optional_user,
    _refresh_verifier_hash,
    _refresh_token_matches,
    _parse_refresh_token,
)
from app.core.ip_cliente import clave_limite
from app.services.salientes_wpp_service import hash_telefono
from app.services.whatsapp_service import formatear_numero_whatsapp
from app.services.email_service import (
    _hash_codigo,
    guardar_codigo_verificacion_email,
    verificar_codigo_email,
    guardar_codigo_recuperacion,
    verificar_codigo_recuperacion,
)
from app.models.usuario import Usuario, EstadoUsuario, AuthProvider
from app.models.refresh_token import RefreshToken


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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db_session):
    usuario = Usuario(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        email="test@argentum.app",
        nombre="Usuario",
        apellido="Test",
        password_hash="hash_dummy",
        password_configurada=True,
        email_verificado=True,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal="ARS",
    )
    db_session.add(usuario)
    db_session.commit()
    return usuario


# ---------------------------------------------------------------------------
# 1. Claves HMAC y funciones auxiliares
# ---------------------------------------------------------------------------

def test_clave_hmac_y_verificacion(monkeypatch):
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    monkeypatch.setattr(settings, "SECRET_KEY", "s1")
    assert clave_hmac() == b"s1"

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "h1")
    monkeypatch.setattr(settings, "SECRET_KEY", "s1")
    assert clave_hmac() == b"h1"

    # claves_hmac_verificacion: ("h1", "s1") -> [b"h1", b"s1"]
    assert claves_hmac_verificacion() == [b"h1", b"s1"]

    # ("", "s1") -> [b"s1"]
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    monkeypatch.setattr(settings, "SECRET_KEY", "s1")
    assert claves_hmac_verificacion() == [b"s1"]

    # ("s1", "s1") -> [b"s1"]
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "s1")
    monkeypatch.setattr(settings, "SECRET_KEY", "s1")
    assert claves_hmac_verificacion() == [b"s1"]


def test_avisar_si_falta_clave_hmac(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.WARNING)

    # con ES_MODO_PC False y HMAC "" -> True y el warning exacto en caplog
    monkeypatch.setattr(settings, "ES_MODO_PC", False)
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    caplog.clear()
    assert avisar_si_falta_clave_hmac() is True
    assert "Falta HMAC_SECRET_KEY: los resúmenes HMAC usan SECRET_KEY." in caplog.text

    # con ES_MODO_PC True -> False
    monkeypatch.setattr(settings, "ES_MODO_PC", True)
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    assert avisar_si_falta_clave_hmac() is False

    # con ES_MODO_PC False y HMAC "h1" -> False
    monkeypatch.setattr(settings, "ES_MODO_PC", False)
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "h1")
    assert avisar_si_falta_clave_hmac() is False


# ---------------------------------------------------------------------------
# 2. Refresh token: hashing, coincidencia y base SQLite
# ---------------------------------------------------------------------------

def test_refresh_verifier_hash(monkeypatch):
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    assert _refresh_verifier_hash("abc") == "3fb5cd59b157bc9818a4dfa8b6d8843350ede86e1a34cf17ef675cff14baae73"

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    assert _refresh_verifier_hash("abc") == "3d0b63a4f681ec46cbdc7ab7ea22210b959ac8bfa2bc2232eb97cc73d1c184fa"


def test_refresh_token_matches(monkeypatch):
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")

    # contra hash viejo -> True
    assert _refresh_token_matches("abc", "3d0b63a4f681ec46cbdc7ab7ea22210b959ac8bfa2bc2232eb97cc73d1c184fa") is True
    # contra hash nuevo -> True
    assert _refresh_token_matches("abc", "3fb5cd59b157bc9818a4dfa8b6d8843350ede86e1a34cf17ef675cff14baae73") is True

    # contra el HMAC de "abc" con la clave "otra_clave" -> False
    hash_otra = hmac.new(b"otra_clave", b"abc", hashlib.sha256).hexdigest()
    assert _refresh_token_matches("abc", hash_otra) is False

    # con "abd" contra cualquiera de los dos -> False
    assert _refresh_token_matches("abd", "3d0b63a4f681ec46cbdc7ab7ea22210b959ac8bfa2bc2232eb97cc73d1c184fa") is False
    assert _refresh_token_matches("abd", "3fb5cd59b157bc9818a4dfa8b6d8843350ede86e1a34cf17ef675cff14baae73") is False


def test_refresh_en_base_sqlite(monkeypatch, db_session, usuario_test):
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")

    token_plain = crear_refresh_token(usuario_test.id, db_session)
    token_id, verifier = _parse_refresh_token(token_plain)
    assert token_id is not None
    assert verifier is not None

    db_token = db_session.execute(select(RefreshToken).where(RefreshToken.token_id == token_id)).scalar_one()
    expected_hash = hmac.new(b"clave_hmac_prueba", verifier.encode("utf-8"), hashlib.sha256).hexdigest()
    assert db_token.token_hash == expected_hash

    res_renovado = renovar_tokens(token_plain, db_session)
    nuevo_access = res_renovado["access_token"]
    nuevo_refresh = res_renovado["refresh_token"]
    assert nuevo_access is not None
    assert nuevo_refresh is not None

    db_session.refresh(db_token)
    assert db_token.revocado is True

    # Legado: token insertado a mano con clave vieja
    hash_legado = hmac.new(b"clave_prueba", b"verificadorlegado", hashlib.sha256).hexdigest()
    legado = RefreshToken(
        token_id="legado1",
        usuario_id=usuario_test.id,
        token_hash=hash_legado,
        fecha_expiracion=datetime.now(timezone.utc) + timedelta(days=7),
        revocado=False,
    )
    db_session.add(legado)
    db_session.commit()

    res_legado = renovar_tokens("legado1.verificadorlegado", db_session)
    nuevo_access_legado = res_legado["access_token"]
    nuevo_refresh_legado = res_legado["refresh_token"]
    assert nuevo_access_legado is not None
    assert nuevo_refresh_legado is not None

    new_id, new_ver = _parse_refresh_token(nuevo_refresh_legado)
    db_new = db_session.execute(select(RefreshToken).where(RefreshToken.token_id == new_id)).scalar_one()
    expected_new_hash = hmac.new(b"clave_hmac_prueba", new_ver.encode("utf-8"), hashlib.sha256).hexdigest()
    assert db_new.token_hash == expected_new_hash


# ---------------------------------------------------------------------------
# 3. Códigos de verificación / recuperación
# ---------------------------------------------------------------------------

def test_hash_codigo_y_verificacion(monkeypatch, db_session):
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    assert _hash_codigo("verificacion_email", "ana@prueba.com", "123456") == "e0742ea36f73b497c176227bb1f61827cd62ba13f342857398109eb53edc5d84"

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    assert _hash_codigo("verificacion_email", "ana@prueba.com", "123456") == "88fe3524916a6d023f82f3c971d96cb1cd0568efeee1ed6812b3f1885547f6f7"

    # Código guardado con HMAC "" y SECRET "clave_prueba"
    guardar_codigo_verificacion_email("ana@prueba.com", "123456")

    # Se verifica bien después de poner HMAC "clave_hmac_prueba"
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    assert verificar_codigo_email("ana@prueba.com", "123456") == (True, None)

    # Lo mismo para recuperación
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    guardar_codigo_recuperacion("ana@prueba.com", "123456")

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    assert verificar_codigo_recuperacion("ana@prueba.com", "123456") is True

    # Código guardado con la clave nueva y código erróneo
    guardar_codigo_verificacion_email("ana@prueba.com", "123456")
    assert verificar_codigo_email("ana@prueba.com", "654321") == (False, "Código incorrecto. Te quedan 2 intentos.")


# ---------------------------------------------------------------------------
# 4. Clave límite (IP) y hash teléfono (WhatsApp)
# ---------------------------------------------------------------------------

def test_clave_limite(monkeypatch):
    class MockRequest:
        headers = {"x-real-ip": "203.0.113.7"}

    req = MockRequest()

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    assert clave_limite(req) == "4d24e36cd4d43678fbb407cd1bfab5da"

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    assert clave_limite(req) == "68f3a20436a27b4c1a65cdce162f0c32"


def test_hash_telefono(monkeypatch):
    numero = "+5493415551234"
    tel_formateado = formatear_numero_whatsapp(numero)

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    expected = hmac.new(b"clave_hmac_prueba", tel_formateado.encode("utf-8"), hashlib.sha256).hexdigest()
    assert hash_telefono(numero) == expected

    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "")
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    old_hash = hash_telefono(numero)
    assert old_hash != expected


# ---------------------------------------------------------------------------
# 5. JWT creación, verificación y compatibilidad
# ---------------------------------------------------------------------------

def test_jwt_creacion_y_firma(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_jwt_prueba")
    monkeypatch.setattr(settings, "HMAC_SECRET_KEY", "clave_hmac_prueba")

    uid = UUID("11111111-1111-1111-1111-111111111111")
    token = crear_access_token(uid, is_admin=True)

    data = jwt.decode(token, "clave_jwt_prueba", algorithms=["HS256"])
    assert data["sub"] == "11111111-1111-1111-1111-111111111111"
    assert data["type"] == "access"
    assert data["is_admin"] is True
    assert 899 <= data["exp"] - data["iat"] <= 900

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(token, "clave_hmac_prueba", algorithms=["HS256"])


def test_jwt_compatibilidad_token_previo(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")
    token_previo = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMTExMTExMS0xMTExLTExMTEtMTExMS0xMTExMTExMTExMTEiLCJleHAiOjQxMDI0NDQ4MDAsImlhdCI6MTc2MjY1MDAwMCwidHlwZSI6ImFjY2VzcyIsImlzX2FkbWluIjpmYWxzZX0.9--PzOKILi1uw6Qpq24soVF_KFqrQl4wozcfWWxpVp4"
    assert verificar_access_token(token_previo) == "11111111-1111-1111-1111-111111111111"


def test_verificar_access_token_invalido(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")

    # 1. Firmado con otra clave
    tok_otra_clave = jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "type": "access", "exp": 4102444800, "iat": 1762650000},
        "otra_clave",
        algorithm="HS256",
    )
    # 2. Vencido (hace 1 hora)
    tok_vencido = jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "type": "access", "exp": 1700000000, "iat": 1699990000},
        "clave_prueba",
        algorithm="HS256",
    )
    # 3. Type "refresh"
    tok_refresh = jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "type": "refresh", "exp": 4102444800, "iat": 1762650000},
        "clave_prueba",
        algorithm="HS256",
    )
    # 4. Sin sub
    tok_sin_sub = jwt.encode(
        {"type": "access", "exp": 4102444800, "iat": 1762650000},
        "clave_prueba",
        algorithm="HS256",
    )
    # 5. Algoritmo "none"
    tok_none = jwt.encode(
        {"sub": "11111111-1111-1111-1111-111111111111", "type": "access", "exp": 4102444800, "iat": 1762650000},
        key=None,
        algorithm="none",
    )
    # 6. Texto plano no-token
    tok_texto = "no-es-un-token"

    for tok in [tok_otra_clave, tok_vencido, tok_refresh, tok_sin_sub, tok_none, tok_texto]:
        with pytest.raises(HTTPException) as exc_info:
            verificar_access_token(tok)
        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Token de acceso inválido o expirado"


def test_get_current_user_y_optional(monkeypatch, db_session, usuario_test):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")

    # Tokens revocados posterior al iat -> 401 "Sesión revocada. Por favor iniciá sesión nuevamente."
    usuario_test.tokens_revocados_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    db_session.commit()

    token = crear_access_token(usuario_test.id)

    scope = {
        "type": "http",
        "headers": [(b"cookie", f"access_token={token}".encode("latin-1"))],
    }
    req = Request(scope=scope)

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(req, db=db_session, token_header=None)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Sesión revocada. Por favor iniciá sesión nuevamente."

    # get_optional_user con token inválido -> None
    scope_bad = {
        "type": "http",
        "headers": [(b"cookie", b"access_token=token-invalido")],
    }
    req_bad = Request(scope=scope_bad)
    assert get_optional_user(req_bad, db=db_session) is None


# ---------------------------------------------------------------------------
# 6. Controles estáticos de AST y texto
# ---------------------------------------------------------------------------

def test_estaticos_ast_y_texto():
    import re
    backend_root = Path(__file__).resolve().parent.parent
    app_root = backend_root / "app"

    # Ningún .py de app\ importa jose (Import o ImportFrom cuyo módulo empiece con "jose")
    for py_file in app_root.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("jose"), f"Import de jose en {py_file}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("jose"), f"ImportFrom de jose en {py_file}: {node.module}"

    # requirements.txt no contiene "python-jose" y tiene la línea exacta "PyJWT[crypto]==2.13.0"
    req_path = backend_root / "requirements.txt"
    req_content = req_path.read_text(encoding="utf-8")
    assert "python-jose" not in req_content
    assert "PyJWT[crypto]==2.13.0" in req_content.splitlines()

    # "jwt.decode(" aparece en app\ solo en app/core/auth.py y app/services/auth_service.py, y en app/core/auth.py una sola vez
    jwt_decode_files = []
    auth_py_path = (app_root / "core" / "auth.py").resolve()
    for py_file in app_root.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if "jwt.decode(" in content:
            jwt_decode_files.append(py_file.resolve())

    expected_jwt_files = [
        (app_root / "core" / "auth.py").resolve(),
        (app_root / "services" / "auth_service.py").resolve(),
    ]
    assert sorted(jwt_decode_files) == sorted(expected_jwt_files)

    auth_content = auth_py_path.read_text(encoding="utf-8")
    assert auth_content.count("jwt.decode(") == 1

    # "SECRET_KEY" aparece en app\ solo en app/core/config.py, app/core/claves.py y app/core/auth.py
    secret_key_files = set()
    for py_file in app_root.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        if re.search(r"\bSECRET_KEY\b", content):
            secret_key_files.add(py_file.resolve())

    expected_secret_key_files = {
        (app_root / "core" / "config.py").resolve(),
        (app_root / "core" / "claves.py").resolve(),
        (app_root / "core" / "auth.py").resolve(),
    }
    assert secret_key_files == expected_secret_key_files

    # En app/core/auth.py "SECRET_KEY" aparece solo dentro de crear_access_token y decodificar_access_token
    auth_tree = ast.parse(auth_content)
    funciones_con_secret_key = set()
    for node in ast.walk(auth_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Attribute) and subnode.attr == "SECRET_KEY":
                    funciones_con_secret_key.add(node.name)
                elif isinstance(subnode, ast.Name) and subnode.id == "SECRET_KEY":
                    funciones_con_secret_key.add(node.name)

    assert funciones_con_secret_key == {"crear_access_token", "decodificar_access_token"}

    # app/core/security.py no define create_access_token ni create_refresh_token
    sec_path = app_root / "core" / "security.py"
    sec_content = sec_path.read_text(encoding="utf-8")
    assert "create_access_token" not in sec_content
    assert "create_refresh_token" not in sec_content
