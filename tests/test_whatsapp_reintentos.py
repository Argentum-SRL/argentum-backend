"""
tests/test_whatsapp_reintentos.py — Pruebas de la política de reintentos ante llamadas a Meta Graph API (R1 a R7).
"""
from __future__ import annotations

from unittest.mock import MagicMock
import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.services import whatsapp_service
from app.services.whatsapp_service import (
    _enviar_registrado,
    _post_meta,
    enviar_whatsapp_template,
)


@pytest.fixture(name="db_session", scope="function", autouse=True)
def db_session_fixture(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.salientes_wpp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.whatsapp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test_token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def test_r1_connect_error_3_veces(monkeypatch):
    """R1 ConnectError 3 veces → 3 pedidos, False, motivo "error_conexion"."""
    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.ConnectError("Failed to connect")
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    res = _post_meta({"messaging_product": "whatsapp"}, max_intentos=3)
    assert res.ok is False
    assert res.motivo == "error_conexion"
    assert res.intentos == 3
    assert mock_client.post.call_count == 3


def test_r2_connect_error_reintento_exitoso(monkeypatch, db_session):
    """R2 ConnectError, ConnectError, 200 con messages[0].id "wamid.X"
    → 3 pedidos, True, fila con wamid_meta "wamid.X"."""
    resp_200 = httpx.Response(
        status_code=200,
        json={"messages": [{"id": "wamid.X"}]},
        request=httpx.Request("POST", "https://graph.facebook.com"),
    )
    mock_client = MagicMock()
    mock_client.post.side_effect = [
        httpx.ConnectError("Conn error 1"),
        httpx.ConnectError("Conn error 2"),
        resp_200,
    ]
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    enviado = _enviar_registrado(
        "5491112345678",
        {"messaging_product": "whatsapp"},
        texto="Hola",
        tipo="respuesta",
        max_intentos=3,
    )
    assert enviado is True
    assert mock_client.post.call_count == 3

    fila = db_session.scalars(
        select(MensajeSalienteWpp).where(MensajeSalienteWpp.wamid_meta == "wamid.X")
    ).first()
    assert fila is not None
    assert fila.enviado is True
    assert fila.wamid_meta == "wamid.X"


def test_r3_read_timeout_no_reintenta(monkeypatch):
    """R3 ReadTimeout → 1 pedido, False, motivo "sin_respuesta_meta".
    Lo mismo con httpx.TimeoutException genérico."""
    # Caso ReadTimeout
    mock_client1 = MagicMock()
    mock_client1.post.side_effect = httpx.ReadTimeout("Read timed out")
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client1)

    res1 = _post_meta({"messaging_product": "whatsapp"}, max_intentos=3)
    assert res1.ok is False
    assert res1.motivo == "sin_respuesta_meta"
    assert res1.intentos == 1
    assert mock_client1.post.call_count == 1

    # Caso TimeoutException genérico
    mock_client2 = MagicMock()
    mock_client2.post.side_effect = httpx.TimeoutException("Generic timeout")
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client2)

    res2 = _post_meta({"messaging_product": "whatsapp"}, max_intentos=3)
    assert res2.ok is False
    assert res2.motivo == "sin_respuesta_meta"
    assert res2.intentos == 1
    assert mock_client2.post.call_count == 1


def test_r4_http_500_no_reintenta(monkeypatch):
    """R4 HTTP 500 → 1 pedido, False, motivo "error_meta_5xx"."""
    resp_500 = httpx.Response(
        status_code=500,
        text="Internal Server Error",
        request=httpx.Request("POST", "https://graph.facebook.com"),
    )
    mock_client = MagicMock()
    mock_client.post.return_value = resp_500
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    res = _post_meta({"messaging_product": "whatsapp"}, max_intentos=3)
    assert res.ok is False
    assert res.motivo == "error_meta_5xx"
    assert res.http_status == 500
    assert res.intentos == 1
    assert mock_client.post.call_count == 1


def test_r5_http_400_rechazado_meta(monkeypatch):
    """R5 HTTP 400 con cuerpo {"error": {"code": 131047}} → 1 pedido, False, motivo "rechazado_meta", codigo_error_meta 131047."""
    resp_400 = httpx.Response(
        status_code=400,
        json={"error": {"code": 131047, "message": "ReEngagement message"}},
        request=httpx.Request("POST", "https://graph.facebook.com"),
    )
    mock_client = MagicMock()
    mock_client.post.return_value = resp_400
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    res = _post_meta({"messaging_product": "whatsapp"}, max_intentos=3)
    assert res.ok is False
    assert res.motivo == "rechazado_meta"
    assert res.codigo_error_meta == 131047
    assert res.http_status == 400
    assert res.intentos == 1
    assert mock_client.post.call_count == 1


def test_r6_http_200_ok(monkeypatch):
    """R6 HTTP 200 → 1 pedido, True."""
    resp_200 = httpx.Response(
        status_code=200,
        json={"messages": [{"id": "wamid.OK"}]},
        request=httpx.Request("POST", "https://graph.facebook.com"),
    )
    mock_client = MagicMock()
    mock_client.post.return_value = resp_200
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    res = _post_meta({"messaging_product": "whatsapp"}, max_intentos=3)
    assert res.ok is True
    assert res.motivo is None
    assert res.wamid_meta == "wamid.OK"
    assert res.http_status == 200
    assert res.intentos == 1
    assert mock_client.post.call_count == 1


def test_r7_enviar_whatsapp_template_max_intentos_1(monkeypatch):
    """R7 enviar_whatsapp_template con max_intentos=1 y ConnectError → 1 pedido."""
    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.ConnectError("Failed to connect")
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    res = enviar_whatsapp_template(
        "5491112345678",
        "alerta_cambio_email",
        "es",
        max_intentos=1,
    )
    assert res is False
    assert mock_client.post.call_count == 1


def test_marcar_leido_y_escribiendo_timeout_3(monkeypatch):
    """marcar_leido_y_escribiendo("wamid.X") hace 1 pedido y con timeout 3."""
    from app.services.whatsapp_service import marcar_leido_y_escribiendo

    mock_client = MagicMock()
    mock_client.post.return_value = httpx.Response(
        status_code=200,
        json={"success": True},
        request=httpx.Request("POST", "https://graph.facebook.com"),
    )
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    ok = marcar_leido_y_escribiendo("wamid.X")
    assert ok is True
    assert mock_client.post.call_count == 1
    _, kwargs = mock_client.post.call_args
    assert kwargs.get("timeout") == 3

