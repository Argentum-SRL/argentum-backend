# -*- coding: utf-8 -*-
"""
tests/test_ip_cliente.py — Pruebas para ip_cliente, clave_limite e integración con Limiter y auth.register.
"""
from __future__ import annotations

import ast
from pathlib import Path
from fastapi.testclient import TestClient
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles

if not hasattr(PGUUID, "_compiler_dispatcher") or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {}):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if not hasattr(JSONB, "_compiler_dispatcher") or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {}):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"

from app.core.config import settings
from app.core.database import Base
from app.core.ip_cliente import clave_limite, ip_cliente
from app.core.limiter import limiter
from app.main import app as fastapi_app


def test_ip_cliente_casos():
    # 1. Encabezado "x-real-ip: 203.0.113.7" y cliente ("10.0.0.1", 1234) -> "203.0.113.7"
    scope1 = {
        "type": "http",
        "headers": [(b"x-real-ip", b"203.0.113.7")],
        "client": ("10.0.0.1", 1234),
    }
    assert ip_cliente(Request(scope1)) == "203.0.113.7"

    # 2. " 203.0.113.7 , 10.0.0.2" -> "203.0.113.7"
    scope2 = {
        "type": "http",
        "headers": [(b"x-real-ip", b" 203.0.113.7 , 10.0.0.2")],
        "client": ("10.0.0.1", 1234),
    }
    assert ip_cliente(Request(scope2)) == "203.0.113.7"

    # 3. Sin encabezado -> "10.0.0.1"
    scope3 = {
        "type": "http",
        "headers": [],
        "client": ("10.0.0.1", 1234),
    }
    assert ip_cliente(Request(scope3)) == "10.0.0.1"

    # 4. Encabezado vacío -> "10.0.0.1"
    scope4 = {
        "type": "http",
        "headers": [(b"x-real-ip", b"")],
        "client": ("10.0.0.1", 1234),
    }
    assert ip_cliente(Request(scope4)) == "10.0.0.1"

    # 5. Sin cliente ni encabezado -> None
    scope5 = {
        "type": "http",
        "headers": [],
        "client": None,
    }
    assert ip_cliente(Request(scope5)) is None


def test_clave_limite_hash_y_privacidad(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba")

    scope_a = {
        "type": "http",
        "headers": [(b"x-real-ip", b"203.0.113.7")],
        "client": ("10.0.0.1", 1234),
    }
    res_a = clave_limite(Request(scope_a))
    assert res_a == "68f3a20436a27b4c1a65cdce162f0c32"

    scope_b = {
        "type": "http",
        "headers": [(b"x-real-ip", b"198.51.100.9")],
        "client": ("10.0.0.1", 1234),
    }
    res_b = clave_limite(Request(scope_b))
    assert res_b == "59dce757b94e0e8a512448e5e4c526ac"

    assert len(res_a) == 32
    assert all(c in "0123456789abcdef" for c in res_a)
    assert "203.0.113.7" not in res_a


def test_limiter_usa_clave_limite():
    key_func = getattr(limiter, "_key_func", None) or getattr(limiter, "key_func", None)
    assert key_func == clave_limite


def test_register_pasa_ip_real_a_turnstile(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.services.rate_limit_service.SessionLocal", TestingSession)

    from app.core.database import get_db

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db

    ips_recibidas = []

    def mock_turnstile(token, ip):
        ips_recibidas.append(ip)
        return False

    monkeypatch.setattr("app.routers.auth.verificar_turnstile_token", mock_turnstile)

    client = TestClient(fastapi_app)
    try:
        # Con encabezado X-Real-IP: 203.0.113.7
        res1 = client.post(
            "/auth/register",
            headers={"X-Real-IP": "203.0.113.7"},
            json={
                "email": "test_ip_1@argentum.test",
                "password": "Password123!",
                "nombre": "Test",
                "apellido": "Ip",
                "turnstile_token": "token123",
            },
        )
        assert res1.status_code == 400
        assert ips_recibidas[-1] == "203.0.113.7"

        # Sin encabezado -> la IP recibida es "testclient"
        res2 = client.post(
            "/auth/register",
            json={
                "email": "test_ip_2@argentum.test",
                "password": "Password123!",
                "nombre": "Test",
                "apellido": "Ip",
                "turnstile_token": "token123",
            },
        )
        assert res2.status_code == 400
        assert ips_recibidas[-1] == "testclient"
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)


def test_estatico_request_client_solo_en_ip_cliente():
    backend_app_dir = Path(__file__).resolve().parent.parent / "app"
    encontrados = []
    for p in backend_app_dir.rglob("*.py"):
        if p.name == "ip_cliente.py":
            continue
        code = p.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "client":
                if isinstance(node.value, ast.Name) and node.value.id == "request":
                    encontrados.append(f"{p.name}:{node.lineno}")
    assert encontrados == [], f"Líneas con request.client fuera de ip_cliente.py: {encontrados}"
