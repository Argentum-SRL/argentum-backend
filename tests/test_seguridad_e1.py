import re
import inspect
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Compilar JSONB como TEXT para SQLite en memoria
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.main import app
from app.core.config import settings
from app.core.database import Base, get_db, engine as app_engine
from app.core.limiter import limiter
from app.core.security import get_password_hash
from app.core.auth import (
    MENSAJE_CUENTA_SUSPENDIDA,
    get_current_user,
    renovar_tokens,
    crear_access_token,
    crear_refresh_token,
    setear_cookies_auth,
    limpiar_cookies_auth,
    _parse_refresh_token,
)
from app.models.usuario import Usuario, EstadoUsuario, AuthProvider, RolUsuario
from app.models.refresh_token import RefreshToken
from app.models.codigo_verificacion import CodigoVerificacion
from app.utils.privacidad import enmascarar_email
from app.services import email_service, alerta_service

# Configuración de base de datos aislada en memoria
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    # Deshabilitar rate limiter en todos los tests
    monkeypatch.setattr(limiter, "enabled", False)
    # Ningún test manda mails reales
    mock_notif = MagicMock(return_value=True)
    mock_email = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.notificacion_email_service.enviar_email_notificacion", mock_notif)
    monkeypatch.setattr("app.services.email_service._enviar_email", mock_email)
    # Sesión SQLite para email_service
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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db_session):
    usuario = Usuario(
        id=uuid4(),
        email="test@argentum.app",
        nombre="Usuario",
        apellido="Test",
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


# ── 1. Rutas eliminadas ───────────────────────────────────────────────────────
def test_01_rutas_eliminadas():
    route_paths = [getattr(r, "path", "") for r in app.routes]
    assert not any("/auth/completar-perfil" in p for p in route_paths), "No debe existir /auth/completar-perfil"
    assert not any("/api/whatsapp/test" in p for p in route_paths), "No debe existir /api/whatsapp/test"


# ── 2. Suspendido + contraseña correcta ─────────────────────────────────────────
def test_02_suspendido_password_correcta(client, db_session, usuario_test):
    usuario_test.estado = EstadoUsuario.INACTIVO
    db_session.commit()

    res = client.post("/auth/login", json={"email": "test@argentum.app", "password": "Clave1234"})
    assert res.status_code == status.HTTP_403_FORBIDDEN
    data = res.json()
    assert data["error"]["message"] == MENSAJE_CUENTA_SUSPENDIDA

    set_cookie = res.headers.get("set-cookie", "")
    assert "access_token" not in set_cookie


# ── 3. Suspendido + contraseña incorrecta ───────────────────────────────────────
def test_03_suspendido_password_incorrecta(client, db_session, usuario_test):
    usuario_test.estado = EstadoUsuario.INACTIVO
    db_session.commit()

    res = client.post("/auth/login", json={"email": "test@argentum.app", "password": "ClaveErronea"})
    assert res.status_code == status.HTTP_401_UNAUTHORIZED
    data = res.json()
    assert data["error"]["message"] == "El email o la contraseña no son correctos. Revisalos e intentá de nuevo."


# ── 4. Suspendido + Google login ──────────────────────────────────────────────
def test_04_suspendido_google_login(client, db_session, usuario_test, monkeypatch):
    usuario_test.estado = EstadoUsuario.INACTIVO
    db_session.commit()

    monkeypatch.setattr(
        "app.routers.auth.verify_google_token",
        lambda token: {
            "sub": "google-sub-123",
            "email": "test@argentum.app",
            "name": "Test User",
            "picture": "https://photo.url",
            "email_verified": True,
        },
    )

    res = client.post("/auth/google", json={"token": "mock_google_token"})
    assert res.status_code == status.HTTP_403_FORBIDDEN
    data = res.json()
    assert data["error"]["message"] == MENSAJE_CUENTA_SUSPENDIDA


# ── 5. renovar_tokens con usuario suspendido y activo ─────────────────────────
def test_05_renovar_tokens_suspension(db_session, usuario_test):
    rt_token = crear_refresh_token(usuario_test.id, db_session)
    token_id, _ = _parse_refresh_token(rt_token)

    # Suspender usuario
    usuario_test.estado = EstadoUsuario.INACTIVO
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        renovar_tokens(rt_token, db_session)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == MENSAJE_CUENTA_SUSPENDIDA

    # Fila de refresh_token queda revocada
    rt_db = db_session.execute(
        select(RefreshToken).where(RefreshToken.token_id == token_id)
    ).scalar_one()
    assert rt_db.revocado is True

    # Reactivar usuario y verificar renovación exitosa
    usuario_test.estado = EstadoUsuario.ACTIVO
    db_session.commit()

    rt_token_activo = crear_refresh_token(usuario_test.id, db_session)
    resultado = renovar_tokens(rt_token_activo, db_session)
    assert "access_token" in resultado
    assert resultado["access_token"] is not None


# ── 6. get_current_user con suspendido ────────────────────────────────────────
def test_06_get_current_user_suspendido(db_session, usuario_test):
    token = crear_access_token(usuario_test.id)

    usuario_test.estado = EstadoUsuario.INACTIVO
    db_session.commit()

    req = MagicMock(cookies={"access_token": token})
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(request=req, db=db_session)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.detail == MENSAJE_CUENTA_SUSPENDIDA


# ── 7. /auth/email/verificar de suspendido ─────────────────────────────────────
def test_07_verificar_email_suspendido(client, db_session, usuario_test, monkeypatch):
    usuario_test.estado = EstadoUsuario.INACTIVO
    db_session.commit()

    monkeypatch.setattr(
        "app.routers.auth.verificar_codigo_email",
        lambda email, codigo: (True, None),
    )

    res = client.post("/auth/email/verificar", json={"email": "test@argentum.app", "codigo": "123456"})
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.json()["error"]["message"] == MENSAJE_CUENTA_SUSPENDIDA

    db_session.refresh(usuario_test)
    assert usuario_test.estado == EstadoUsuario.INACTIVO


# ── 8. Atributos de Cookies (SameSite=Lax, Path) ───────────────────────────────
def test_08_cookies_samesite_y_paths(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 15)
    monkeypatch.setattr(settings, "REFRESH_TOKEN_EXPIRE_DAYS", 30)

    # 1. setear_cookies_auth
    resp = Response()
    setear_cookies_auth(resp, access_token="access_val", refresh_token="refresh_val")

    raw_cookies = [v.decode("latin-1") for k, v in resp.raw_headers if k.lower() == b"set-cookie"]
    assert len(raw_cookies) > 0

    for c in raw_cookies:
        lower = c.lower()
        assert "samesite=lax" in lower
        assert "samesite=none" not in lower

    # refresh_token con valor tiene Path=/auth
    rt_set = [c for c in raw_cookies if "refresh_token=refresh_val" in c]
    assert len(rt_set) == 1
    assert "path=/auth" in rt_set[0].lower()

    # Cookie que borra refresh_token con Path=/
    rt_del = [c for c in raw_cookies if "refresh_token=" in c and ("max-age=0" in c.lower() or "expires=" in c.lower())]
    assert len(rt_del) >= 1
    assert any("path=/" in c.lower() and "path=/auth" not in c.lower() for c in rt_del)

    # access_token con Path=/
    at_set = [c for c in raw_cookies if "access_token=access_val" in c]
    assert len(at_set) == 1
    assert "path=/" in at_set[0].lower() and "path=/auth" not in at_set[0].lower()

    # 2. limpiar_cookies_auth
    resp_clean = Response()
    limpiar_cookies_auth(resp_clean)

    raw_cookies_clean = [v.decode("latin-1") for k, v in resp_clean.raw_headers if k.lower() == b"set-cookie"]
    for c in raw_cookies_clean:
        lower = c.lower()
        assert "samesite=lax" in lower
        assert "samesite=none" not in lower

    rt_clean = [c for c in raw_cookies_clean if "refresh_token=" in c]
    assert any("path=/auth" in c.lower() for c in rt_clean)
    assert any("path=/" in c.lower() and "path=/auth" not in c.lower() for c in rt_clean)


# ── 9. Middleware de Origin ───────────────────────────────────────────────────
def test_09_middleware_origin(client):
    # POST con Origin no permitido -> 403 ORIGEN_NO_PERMITIDO
    r_bad = client.post("/auth/logout", headers={"Origin": "https://sitio-malo.com"})
    assert r_bad.status_code == status.HTTP_403_FORBIDDEN
    assert r_bad.json()["error"]["code"] == "ORIGEN_NO_PERMITIDO"

    # POST con Origin del frontend -> 200
    r_good = client.post("/auth/logout", headers={"Origin": settings.FRONTEND_URL})
    assert r_good.status_code == status.HTTP_200_OK

    # POST sin Origin -> 200
    r_no_origin = client.post("/auth/logout")
    assert r_no_origin.status_code == status.HTTP_200_OK

    # GET con Origin malo -> 200
    r_get = client.get("/", headers={"Origin": "https://sitio-malo.com"})
    assert r_get.status_code == status.HTTP_200_OK

    # POST /api/whatsapp/webhook con Origin malo -> no da ORIGEN_NO_PERMITIDO
    r_wpp = client.post("/api/whatsapp/webhook", headers={"Origin": "https://sitio-malo.com"}, content=b"")
    data_wpp = r_wpp.json() if r_wpp.headers.get("content-type", "").startswith("application/json") else {}
    if isinstance(data_wpp, dict) and "error" in data_wpp and isinstance(data_wpp["error"], dict):
        assert data_wpp["error"].get("code") != "ORIGEN_NO_PERMITIDO"


# ── 10. POST /reporte-error no envía emails ───────────────────────────────────
def test_10_reporte_error_no_envia_alerta(client, monkeypatch):
    mock_alerta = MagicMock()
    monkeypatch.setattr("app.services.alerta_service.enviar_alerta_admin", mock_alerta)

    payload = {
        "mensaje": "TypeError: null is not an object",
        "stack": "Error: ...\n    at Component",
        "ruta": "/app/dashboard",
        "componente": "Dashboard",
    }
    res = client.post("/reporte-error", json=payload)
    assert res.status_code == status.HTTP_204_NO_CONTENT
    assert mock_alerta.call_count == 0


# ── 11. Tope diario de alertas al admin ────────────────────────────────────────
def test_11_tope_alertas_admin(monkeypatch):
    monkeypatch.setenv("ADMIN_ALERT_EMAIL", "admin@argentum.app")
    alerta_service._alerta_cache.clear()
    from app.utils.fecha import hoy_argentina
    with alerta_service._alerta_lock:
        alerta_service._alertas_enviadas_hoy = 0
        alerta_service._fecha_contador_alertas = hoy_argentina()

    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.email_service._enviar_email", mock_enviar)

    resultados = [
        alerta_service.enviar_alerta_admin(asunto="Asunto Test", cuerpo="Cuerpo Test", clave=f"clave_test_{i}")
        for i in range(25)
    ]

    assert mock_enviar.call_count == 20
    assert resultados[:20] == [True] * 20
    assert resultados[20:] == [False] * 5


# ── 12. Generación y almacenamiento seguro de códigos ─────────────────────────
def test_12_codigos_hash_y_secrets(db_session):
    # Verificación estática de que random.randint no está en email_service
    src = inspect.getsource(email_service)
    assert "random.randint" not in src, "email_service no debe contener random.randint"

    # 200 llamadas a _generar_codigo coinciden con ^\d{6}$
    for _ in range(200):
        code = email_service._generar_codigo()
        assert re.match(r"^\d{6}$", code) is not None

    # Guardar verificación email con mayúsculas
    email_service.guardar_codigo_verificacion_email("Test@Mail.com", "123456")

    fila = db_session.execute(
        select(CodigoVerificacion).where(
            CodigoVerificacion.tipo == "verificacion_email",
            CodigoVerificacion.identificador == "test@mail.com",
            CodigoVerificacion.consumido == False,
        )
    ).scalar_one()

    assert fila.codigo != "123456"
    assert len(fila.codigo) == 64

    # Verificar con código erróneo
    ok, err = email_service.verificar_codigo_email("test@mail.com", "654321")
    assert ok is False
    assert err == "Código incorrecto. Te quedan 2 intentos."

    # Verificar con código correcto
    ok2, err2 = email_service.verificar_codigo_email("test@mail.com", "123456")
    assert ok2 is True
    assert err2 is None

    # Verificar de nuevo con código correcto (ya expirado/consumido)
    ok3, err3 = email_service.verificar_codigo_email("test@mail.com", "123456")
    assert ok3 is False
    assert err3 == "El código ya expiró. Pedí uno nuevo."

    # Guardar y verificar código de recuperación
    email_service.guardar_codigo_recuperacion("recup@test.com", "987654")

    fila_recup = db_session.execute(
        select(CodigoVerificacion).where(
            CodigoVerificacion.tipo == "recuperacion_password",
            CodigoVerificacion.identificador == "recup@test.com",
            CodigoVerificacion.consumido == False,
        )
    ).scalar_one()

    assert fila_recup.codigo != "987654"
    assert len(fila_recup.codigo) == 64

    res_recup = email_service.verificar_codigo_recuperacion("recup@test.com", "987654")
    assert res_recup is True


# ── 13. Límite de login por cuenta (10 intentos / 60 minutos) ──────────────────
def test_13_limite_login_cuenta(client, db_session, usuario_test, monkeypatch):
    mock_notif = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.notificacion_email_service.enviar_email_notificacion", mock_notif)

    # 10 intentos fallidos
    for i in range(10):
        res = client.post("/auth/login", json={"email": "test@argentum.app", "password": "PasswordInvalida"})
        assert res.status_code == status.HTTP_401_UNAUTHORIZED
        assert res.json()["error"]["message"] == "El email o la contraseña no son correctos. Revisalos e intentá de nuevo."

    # Se envió exactamente 1 email de aviso (al tercer intento)
    assert mock_notif.call_count == 1

    # Intento 11 con contraseña correcta -> 429
    res_11 = client.post("/auth/login", json={"email": "test@argentum.app", "password": "Clave1234"})
    assert res_11.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert res_11.json()["error"]["message"] == "Hubo demasiados intentos fallidos con esta cuenta. Esperá una hora o recuperá tu contraseña."

    # Mover ultimo_intento_fallido_at a 61 minutos atrás
    db_session.refresh(usuario_test)
    usuario_test.ultimo_intento_fallido_at = datetime.now(timezone.utc) - timedelta(minutes=61)
    db_session.commit()

    # Contraseña correcta -> 200 y se resetean los intentos
    res_ok = client.post("/auth/login", json={"email": "test@argentum.app", "password": "Clave1234"})
    assert res_ok.status_code == status.HTTP_200_OK

    db_session.refresh(usuario_test)
    assert usuario_test.intentos_fallidos_login == 0


# ── 14. SQLAlchemy hide_parameters ────────────────────────────────────────────
def test_14_hide_parameters():
    assert app_engine.hide_parameters is True


# ── 15. Scrubbing de PII en logs de Google login ───────────────────────────────
def test_15_privacidad_logs_google(client, caplog, monkeypatch):
    monkeypatch.setattr(
        "app.routers.auth.verify_google_token",
        lambda token: {
            "sub": "google-sub-999",
            "email": "nuevo.usuario@test.com",
            "name": "Nuevito Perez",
            "given_name": "Nuevito",
            "family_name": "Perez",
            "picture": "https://lh3.googleusercontent.com/photo",
            "email_verified": True,
        },
    )

    with caplog.at_level(logging.DEBUG):
        res = client.post("/auth/google", json={"token": "mock_google_token_new"})

    assert res.status_code == status.HTTP_200_OK
    assert "nuevo.usuario@test.com" not in caplog.text
    assert "Nuevito" not in caplog.text


# ── 16. Enmascarar email ──────────────────────────────────────────────────────
def test_16_enmascarar_email():
    assert enmascarar_email("giordaninosebas@gmail.com") == "gi***@gmail.com"
    assert enmascarar_email("a@b.com") == "a***@b.com"
    assert enmascarar_email(None) == "(sin email)"
    assert enmascarar_email("sinarroba") == "***"
