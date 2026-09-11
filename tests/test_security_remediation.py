import urllib.parse
from uuid import uuid4
from fastapi import Response
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.services.ai_service import extraer_concepto_mensaje, sanitizar_descripcion
from app.core.auth import (
    _parse_refresh_token,
    _refresh_verifier_hash,
    _refresh_secret_hash,
    _refresh_token_matches,
    setear_cookies_auth,
    limpiar_cookies_auth,
)
from app.core.config import settings
from app.main import app


# ---------------------------------------------------------------------------
# 1. Tests de ReDoS / Expresión Regular Polinómica (ai_service.py)
# ---------------------------------------------------------------------------

def test_extraer_concepto_mensaje_sin_redos():
    # Números estándar (enteros y decimales)
    assert extraer_concepto_mensaje("gasté 5000 en coto") == "Coto"
    assert extraer_concepto_mensaje("pagué 1500.50 en farmacia") == "Farmacia"
    assert extraer_concepto_mensaje("1500,50 cafe") == "Cafe"
    assert extraer_concepto_mensaje("$2500 helado") == "Helado"
    
    # Secuencia larga de dígitos que causaría retroceso polinómico en la regex anterior
    long_digits = "1" * 5000 + " panaderia"
    res = extraer_concepto_mensaje(long_digits)
    assert res == "Panaderia"


def test_sanitizar_descripcion_sin_redos():
    assert sanitizar_descripcion("Supermercado 1234.56") == "Supermercado"
    assert sanitizar_descripcion("Coto 5000,00") == "Coto"
    
    long_digits = "Farmacia " + "9" * 5000
    res = sanitizar_descripcion(long_digits)
    assert res == "Farmacia"


# ---------------------------------------------------------------------------
# 2. Tests de Refresh Token Verifier y Cookies (auth.py)
# ---------------------------------------------------------------------------

def test_parse_y_match_refresh_token():
    token_plain = "tok1234567890abc.verif1234567890abcdefghijklmnopqrstuvwxyz"
    token_id, verifier = _parse_refresh_token(token_plain)
    assert token_id == "tok1234567890abc"
    assert verifier == "verif1234567890abcdefghijklmnopqrstuvwxyz"

    # Verificar hash y match
    h = _refresh_verifier_hash(verifier)
    assert _refresh_token_matches(verifier, h) is True
    assert _refresh_token_matches("otro_verifier", h) is False

    # Verificar compatibilidad de alias
    assert _refresh_secret_hash(verifier) == h


def test_setear_y_limpiar_cookies_auth():
    resp = Response()
    setear_cookies_auth(resp, "access_fake", "refresh_fake.verifier", settings)

    cookies = [header for header in resp.headers.raw if header[0] == b"set-cookie"]
    cookie_str = " ".join([c[1].decode("latin1") for c in cookies])

    assert "access_token=access_fake" in cookie_str
    assert "refresh_token=refresh_fake.verifier" in cookie_str
    assert "HttpOnly" in cookie_str

    # Limpiar cookies
    resp_clean = Response()
    limpiar_cookies_auth(resp_clean)
    cookies_clean = [header for header in resp_clean.headers.raw if header[0] == b"set-cookie"]
    cookie_clean_str = " ".join([c[1].decode("latin1") for c in cookies_clean])
    assert 'access_token=""' in cookie_clean_str or 'access_token=;' in cookie_clean_str
    assert 'refresh_token=""' in cookie_clean_str or 'refresh_token=;' in cookie_clean_str


# ---------------------------------------------------------------------------
# 3. Tests de Redirección Segura en Email Verification Link (routers/auth.py)
# ---------------------------------------------------------------------------

def test_verificar_email_link_redireccion_segura_codigo_invalido():
    client = TestClient(app)
    
    with patch("app.routers.auth.verificar_codigo_email", return_value=(False, "Código inválido o expirado")):
        # Intentar inyección de parámetros o URLs arbitrarias en el email
        malicious_email = "victim@domain.com&redirect=https://evil.com#frag"
        resp = client.get(
            f"/auth/email/verificar-link?email={urllib.parse.quote(malicious_email)}&codigo=123456",
            follow_redirects=False,
        )
        assert resp.status_code == 307 or resp.status_code == 302 or resp.status_code == 303
        location = resp.headers["location"]

        # El destino debe comenzar estrictamente con FRONTEND_URL/auth/verificar-email?
        expected_prefix = f"{settings.FRONTEND_URL}/auth/verificar-email?"
        assert location.startswith(expected_prefix)

        # Parsear query params del redirect
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)

        # El email malicioso debe estar 100% contenido en el valor de 'email' y no haber generado otros parámetros
        assert params["email"] == [malicious_email.lower()]
        assert "redirect" not in params
        assert "error" in params


def test_verificar_email_link_redireccion_segura_usuario_no_encontrado():
    client = TestClient(app)

    with patch("app.routers.auth.verificar_codigo_email", return_value=(True, None)), \
         patch("app.routers.auth.get_db") as mock_db:
        
        mock_session = MagicMock()
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        app.dependency_overrides[app.dependency_overrides.get("get_db") or mock_db] = lambda: mock_session

        resp = client.get(
            "/auth/email/verificar-link?email=inexistente@test.com&codigo=123456",
            follow_redirects=False,
        )
        
        # Limpiar overrides
        app.dependency_overrides.clear()

        assert resp.status_code in (302, 303, 307)
        location = resp.headers["location"]
        assert location.startswith(f"{settings.FRONTEND_URL}/auth/verificar-email?")
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        assert params["email"] == ["inexistente@test.com"]
        assert params["error"] == ["No encontramos una cuenta con esos datos."]
