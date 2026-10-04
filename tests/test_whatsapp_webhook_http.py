"""
tests/test_whatsapp_webhook_http.py — Pruebas de integración HTTP reales para el webhook de WhatsApp.
Garantiza que los endpoints de FastAPI (/api/whatsapp/webhook y /api/whatsapp/test) estén registrados,
que el handshake de Meta funcione correctamente, que la firma HMAC-SHA256 se valide, y que el
flujo de vinculación de usuarios procese los eventos entrantes sin retornar 404.
"""
import hashlib
import hmac
import json
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.codigo_verificacion import CodigoVerificacion
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import whatsapp_service


TEST_VERIFY_TOKEN = "test_verify_token_12345"
TEST_APP_SECRET = "test_app_secret_abcdef1234567890"


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("app.routers.whatsapp_ia.SessionLocal", side_effect=TestingSession),
        patch("app.services.whatsapp_service.SessionLocal", side_effect=TestingSession),
        patch("app.core.database.SessionLocal", side_effect=TestingSession),
        patch.object(settings, "WHATSAPP_VERIFY_TOKEN", TEST_VERIFY_TOKEN),
        patch.object(settings, "WHATSAPP_APP_SECRET", TEST_APP_SECRET),
        patch("app.services.whatsapp_service.enviar_whatsapp", return_value=True),
        patch("app.services.whatsapp_service.marcar_leido_y_escribiendo", return_value=True),
    ):
        try:
            yield session
        finally:
            session.close()
            app.dependency_overrides.pop(get_db, None)


def _calcular_firma_meta(body_bytes: bytes, secret: str = TEST_APP_SECRET) -> str:
    sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def test_routes_registered_in_fastapi_app():
    """
    Verifica que las rutas de WhatsApp estén efectivamente registradas en app.routes
    con los métodos esperados y sin duplicados.
    """
    rutas = [(r.path, getattr(r, "methods", set())) for r in app.routes if hasattr(r, "path")]
    
    webhook_routes = [r for r in rutas if r[0] == "/api/whatsapp/webhook"]
    test_routes = [r for r in rutas if r[0] == "/api/whatsapp/test"]

    # 1. /api/whatsapp/webhook debe existir
    assert len(webhook_routes) >= 1, "La ruta /api/whatsapp/webhook no está registrada en app.routes"

    metodos_webhook = set()
    for _, methods in webhook_routes:
        metodos_webhook.update(methods)

    # 2. Métodos GET y POST deben estar soportados
    assert "GET" in metodos_webhook, "GET no está registrado en /api/whatsapp/webhook"
    assert "POST" in metodos_webhook, "POST no está registrado en /api/whatsapp/webhook"

    # 3. /api/whatsapp/test debe existir con POST
    assert len(test_routes) >= 1, "La ruta /api/whatsapp/test no está registrada en app.routes"
    metodos_test = set()
    for _, methods in test_routes:
        metodos_test.update(methods)
    assert "POST" in metodos_test, "POST no está registrado en /api/whatsapp/test"


def test_get_webhook_verify_handshake_success(db_session):
    """
    TEST 1: GET /api/whatsapp/webhook con verify_token correcto y hub.mode=subscribe
    debe responder HTTP 200 con el hub.challenge en texto plano.
    """
    client = TestClient(app)
    challenge = "CHALLENGE_ACCEPTED_98765"
    response = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": TEST_VERIFY_TOKEN,
            "hub.challenge": challenge,
        },
    )
    assert response.status_code == 200
    assert response.text == challenge


def test_get_webhook_verify_handshake_invalid_token(db_session):
    """
    TEST 2: GET /api/whatsapp/webhook con token incorrecto debe responder HTTP 403 Forbidden.
    """
    client = TestClient(app)
    response = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "token_completamente_invalido",
            "hub.challenge": "CHALLENGE_REJECTED",
        },
    )
    assert response.status_code == 403
    assert "Verificación fallida" in response.text


def test_get_webhook_missing_params(db_session):
    """
    GET /api/whatsapp/webhook sin parámetros de Meta debe responder HTTP 400 Bad Request.
    """
    client = TestClient(app)
    response = client.get("/api/whatsapp/webhook")
    assert response.status_code == 400


def test_post_webhook_missing_hmac_signature(db_session):
    """
    TEST 4: POST /api/whatsapp/webhook sin header X-Hub-Signature-256 debe responder HTTP 403.
    """
    client = TestClient(app)
    payload = json.dumps({"entry": []}).encode("utf-8")
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 403
    assert "Firma inválida" in response.text


def test_post_webhook_invalid_hmac_signature(db_session):
    """
    TEST 5: POST /api/whatsapp/webhook con firma HMAC incorrecta debe responder HTTP 403.
    """
    client = TestClient(app)
    payload = json.dumps({"entry": []}).encode("utf-8")
    response = client.post(
        "/api/whatsapp/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        },
    )
    assert response.status_code == 403
    assert "Firma inválida" in response.text


def test_post_webhook_valid_hmac_empty_payload(db_session):
    """
    TEST 3: POST /api/whatsapp/webhook con payload válido + HMAC válido NO debe dar 404,
    debe responder HTTP 200 OK de inmediato.
    """
    client = TestClient(app)
    payload = json.dumps({"entry": []}).encode("utf-8")
    firma = _calcular_firma_meta(payload)

    response = client.post(
        "/api/whatsapp/webhook",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": firma,
        },
    )
    assert response.status_code == 200
    assert response.text == "OK"


def test_post_webhook_status_callback(db_session):
    """
    POST /api/whatsapp/webhook con eventos de status (delivered, read, sent)
    debe responder 200 OK rápidamente sin error.
    """
    client = TestClient(app)
    payload_dict = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.HBgLMTIzNDU2Nzg5MA==",
                                    "status": "delivered",
                                    "recipient_id": "5491122334455",
                                    "timestamp": "1710000000",
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body_bytes = json.dumps(payload_dict).encode("utf-8")
    firma = _calcular_firma_meta(body_bytes)

    response = client.post(
        "/api/whatsapp/webhook",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": firma,
        },
    )
    assert response.status_code == 200
    assert response.text == "OK"


def test_post_webhook_deduplication_wamid(db_session):
    """
    Deduplicación por wamid: dos requests con el mismo wamid deben ser aceptados
    con HTTP 200, pero el segundo no debe volver a persistirse ni duplicar procesamiento.
    """
    client = TestClient(app)
    wamid_test = f"wamid.TEST_{uuid4().hex[:12]}"
    payload_dict = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": wamid_test,
                                    "from": "5491122334455",
                                    "type": "text",
                                    "text": {"body": "hola bot"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body_bytes = json.dumps(payload_dict).encode("utf-8")
    firma = _calcular_firma_meta(body_bytes)

    # Primer envío
    resp1 = client.post(
        "/api/whatsapp/webhook",
        content=body_bytes,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma},
    )
    assert resp1.status_code == 200

    # Verificar que quedó registrado en MensajeWhatsappProcesado
    registro = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == wamid_test)
    ).scalar_one_or_none()
    assert registro is not None
    assert registro.wamid == wamid_test

    # Segundo envío con mismo wamid (reintento de Meta)
    resp2 = client.post(
        "/api/whatsapp/webhook",
        content=body_bytes,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": firma},
    )
    assert resp2.status_code == 200

    # No debe haber duplicado en DB
    total = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == wamid_test)
    ).scalars().all()
    assert len(total) == 1


def test_flujo_vinculacion_e2e_regresion(db_session):
    """
    TEST DE REGRESIÓN DEL FLUJO DE VINCULACIÓN:
    1. Usuario existe en la DB sin teléfono vinculado.
    2. Se genera código de vinculación para el usuario.
    3. Llega un payload de WhatsApp al webhook con el código.
    4. El webhook procesa el mensaje a través de BackgroundTasks.
    5. El código se consume en la tabla codigos_verificacion.
    6. El usuario queda vinculado y con telefono_verificado = True.
    """
    # 1. Crear usuario de prueba
    usuario_id = uuid4()
    usuario = Usuario(
        id=usuario_id,
        email="vinculacion_test@argentum.com",
        nombre="Juan",
        apellido="Prueba",
        telefono=None,
        telefono_normalizado=None,
        telefono_verificado=False,
        moneda_principal=Moneda.ARS,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
    )
    db_session.add(usuario)
    db_session.commit()

    # 2. Generar código de vinculación
    codigo, exp_ts = whatsapp_service.generar_codigo_vinculacion(usuario_id)
    assert len(codigo) == 6

    # Verificar que el código está en DB como no consumido
    cod_db = db_session.execute(
        select(CodigoVerificacion).where(CodigoVerificacion.codigo == codigo)
    ).scalar_one_or_none()
    assert cod_db is not None
    assert cod_db.consumido is False
    assert cod_db.identificador == str(usuario_id)

    # 3. Simular mensaje entrante de Meta que envía el usuario desde su WhatsApp
    telefono_remitente = "5491144556677"
    wamid_msg = f"wamid.VINC_{uuid4().hex[:10]}"
    payload_dict = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": wamid_msg,
                                    "from": telefono_remitente,
                                    "type": "text",
                                    "text": {
                                        "body": f"Hola, quiero vincular mi cuenta de Argentum. Codigo: {codigo}"
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    body_bytes = json.dumps(payload_dict).encode("utf-8")
    firma = _calcular_firma_meta(body_bytes)

    # 4. Enviar al endpoint HTTP real vía TestClient
    # TestClient de FastAPI ejecuta las BackgroundTasks inmediatamente de forma síncrona
    client = TestClient(app)
    response = client.post(
        "/api/whatsapp/webhook",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": firma,
        },
    )
    assert response.status_code == 200
    assert response.text == "OK"

    # 5. Verificar que el código fue consumido en la base de datos
    db_session.expire_all()
    cod_consumido = db_session.execute(
        select(CodigoVerificacion).where(CodigoVerificacion.codigo == codigo)
    ).scalar_one_or_none()
    assert cod_consumido is not None
    assert cod_consumido.consumido is True
    assert cod_consumido.consumido_en is not None

    # 6. Verificar que el usuario quedó con el teléfono vinculado y verificado
    usuario_actualizado = db_session.execute(
        select(Usuario).where(Usuario.id == usuario_id)
    ).scalar_one()
    assert usuario_actualizado.telefono_verificado is True
    assert usuario_actualizado.telefono == f"+{telefono_remitente}"
    assert usuario_actualizado.telefono_normalizado == "1144556677"
