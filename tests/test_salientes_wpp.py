"""
tests/test_salientes_wpp.py — Pruebas de registro de mensajes salientes, tope diario,
estados de webhook Meta, monitor admin y purga (S1 a S6).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if not hasattr(PGUUID, "_compiler_dispatcher") or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {}):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if not hasattr(JSONB, "_compiler_dispatcher") or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {}):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"

from app.core.auth import get_current_admin_user, get_current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app as fastapi_app
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import whatsapp_service
from app.services.salientes_wpp_service import (
    hash_telefono,
    purgar_salientes_antiguos,
    resumen_salientes,
)
from app.services.usuario_service import eliminar_usuario
from app.services.whatsapp_service import enviar_whatsapp, enviar_whatsapp_template
from app.utils.fecha import TZ_ARGENTINA

TEST_APP_SECRET = "secret_para_tests_webhook_salientes_12345"


def _calcular_firma(body_bytes: bytes) -> str:
    sig = hmac.new(TEST_APP_SECRET.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


@pytest.fixture(name="db_session", scope="function")
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

    def override_get_db():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    fastapi_app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.salientes_wpp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.whatsapp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.routers.whatsapp_ia.SessionLocal", TestingSession)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", TEST_APP_SECRET)
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test_token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456789")

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        fastapi_app.dependency_overrides.pop(get_db, None)
        fastapi_app.dependency_overrides.pop(get_current_admin_user, None)
        fastapi_app.dependency_overrides.pop(get_current_user, None)


def _crear_usuario(db: Session, rol: RolUsuario = RolUsuario.USUARIO) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"usr_{uuid4().hex[:6]}@argentum.com",
        telefono="+5491112345678",
        telefono_normalizado="+5491112345678",
        auth_provider=AuthProvider.EMAIL,
        rol=rol,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="User",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_s1_tope_diario_salientes(db_session, monkeypatch):
    """S1 Tope: 150 filas enviadas hoy para ese teléfono → el siguiente enviar_whatsapp no llama a Meta,
    devuelve False y registra enviado=False con motivo "tope_diario_salientes";
    con 149 sale; 150 filas de ayer no cuentan; con 150 de hoy, la plantilla "alerta_cambio_contrasena" sale igual."""
    numero = "+5491112345678"
    tel_hash = hash_telefono(numero)

    mock_client = MagicMock()
    mock_client.post.return_value = httpx.Response(
        status_code=200,
        json={"messages": [{"id": "wamid.test"}]},
        request=httpx.Request("POST", "https://graph.facebook.com"),
    )
    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", lambda: mock_client)

    # 150 filas de ayer -> no cuentan
    ayer = datetime.now(timezone.utc) - timedelta(days=1)
    for _ in range(150):
        db_session.add(MensajeSalienteWpp(
            id=uuid4(),
            fecha=ayer,
            telefono_hash=tel_hash,
            tipo="respuesta",
            enviado=True,
        ))
    db_session.commit()

    # Debe salir porque las de ayer no cuentan
    res = enviar_whatsapp(numero, "Mensaje de hoy")
    assert res is True
    assert mock_client.post.call_count == 1

    # Limpiar filas creadas
    db_session.query(MensajeSalienteWpp).delete()
    db_session.commit()

    # 149 filas de hoy
    hoy = datetime.now(timezone.utc)
    for _ in range(149):
        db_session.add(MensajeSalienteWpp(
            id=uuid4(),
            fecha=hoy,
            telefono_hash=tel_hash,
            tipo="respuesta",
            enviado=True,
        ))
    db_session.commit()

    # Con 149 sale (es la 150)
    mock_client.post.reset_mock()
    res150 = enviar_whatsapp(numero, "Mensaje 150")
    assert res150 is True
    assert mock_client.post.call_count == 1

    # Ahora hay 150 filas de hoy: el siguiente enviar_whatsapp no llama a Meta
    mock_client.post.reset_mock()
    res151 = enviar_whatsapp(numero, "Mensaje 151")
    assert res151 is False
    assert mock_client.post.call_count == 0

    fila_bloqueada = db_session.scalars(
        select(MensajeSalienteWpp).where(
            MensajeSalienteWpp.telefono_hash == tel_hash,
            MensajeSalienteWpp.enviado == False,
        )
    ).first()
    assert fila_bloqueada is not None
    assert fila_bloqueada.motivo_no_enviado == "tope_diario_salientes"

    # Con 150 de hoy, plantilla exenta "alerta_cambio_contrasena" sale igual
    mock_client.post.reset_mock()
    res_tpl = enviar_whatsapp_template(numero, "alerta_cambio_contrasena", "es")
    assert res_tpl is True
    assert mock_client.post.call_count == 1


def test_s2_estados_meta_webhook(db_session):
    """S2 Estados por el webhook (POST /api/whatsapp/webhook firmado):
    fila con wamid_meta "wamid.X" en "sent"; status delivered con pricing regular →
    delivered, regular, service; después read sin pricing → read y regular;
    después sent tardío → sigue read.
    Otra fila failed con errors code 131047 → failed, 131047.
    wamid desconocido → 200 y nada cambia."""
    # Fila 1
    m1 = MensajeSalienteWpp(
        id=uuid4(),
        fecha=datetime.now(timezone.utc),
        tipo="respuesta",
        enviado=True,
        wamid_meta="wamid.X",
        estado_meta="sent",
    )
    # Fila 2
    m2 = MensajeSalienteWpp(
        id=uuid4(),
        fecha=datetime.now(timezone.utc),
        tipo="respuesta",
        enviado=True,
        wamid_meta="wamid.Y",
        estado_meta="sent",
    )
    db_session.add_all([m1, m2])
    db_session.commit()

    client = TestClient(fastapi_app)

    # 1. Status delivered con pricing
    payload1 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "entry1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"display_phone_number": "123", "phone_number_id": "456"},
                    "statuses": [{
                        "id": "wamid.X",
                        "status": "delivered",
                        "timestamp": "1700000000",
                        "recipient_id": "5491112345678",
                        "pricing": {
                            "type": "regular",
                            "category": "service",
                            "pricing_model": "PMP",
                            "billable": True,
                        },
                    }],
                },
            }],
        }],
    }
    body1 = json.dumps(payload1).encode("utf-8")
    resp1 = client.post(
        "/api/whatsapp/webhook",
        content=body1,
        headers={"X-Hub-Signature-256": _calcular_firma(body1), "Content-Type": "application/json"},
    )
    assert resp1.status_code == 200
    db_session.refresh(m1)
    assert m1.estado_meta == "delivered"
    assert m1.tipo_cobro == "regular"
    assert m1.categoria_cobro == "service"

    # 2. Status read sin pricing
    payload2 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "entry1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "statuses": [{
                        "id": "wamid.X",
                        "status": "read",
                        "timestamp": "1700000010",
                    }],
                },
            }],
        }],
    }
    body2 = json.dumps(payload2).encode("utf-8")
    resp2 = client.post(
        "/api/whatsapp/webhook",
        content=body2,
        headers={"X-Hub-Signature-256": _calcular_firma(body2), "Content-Type": "application/json"},
    )
    assert resp2.status_code == 200
    db_session.refresh(m1)
    assert m1.estado_meta == "read"
    assert m1.tipo_cobro == "regular"

    # 3. Sent tardío -> sigue read
    payload3 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "entry1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "statuses": [{
                        "id": "wamid.X",
                        "status": "sent",
                        "timestamp": "1700000020",
                    }],
                },
            }],
        }],
    }
    body3 = json.dumps(payload3).encode("utf-8")
    resp3 = client.post(
        "/api/whatsapp/webhook",
        content=body3,
        headers={"X-Hub-Signature-256": _calcular_firma(body3), "Content-Type": "application/json"},
    )
    assert resp3.status_code == 200
    db_session.refresh(m1)
    assert m1.estado_meta == "read"

    # 4. Failed en wamid.Y
    payload4 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "entry1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "statuses": [{
                        "id": "wamid.Y",
                        "status": "failed",
                        "errors": [{"code": 131047, "title": "Re-engagement"}],
                    }],
                },
            }],
        }],
    }
    body4 = json.dumps(payload4).encode("utf-8")
    resp4 = client.post(
        "/api/whatsapp/webhook",
        content=body4,
        headers={"X-Hub-Signature-256": _calcular_firma(body4), "Content-Type": "application/json"},
    )
    assert resp4.status_code == 200
    db_session.refresh(m2)
    assert m2.estado_meta == "failed"
    assert m2.codigo_error_meta == 131047

    # 5. Wamid desconocido -> 200 y nada cambia
    payload5 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "entry1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "statuses": [{
                        "id": "wamid.DESCONOCIDO",
                        "status": "delivered",
                    }],
                },
            }],
        }],
    }
    body5 = json.dumps(payload5).encode("utf-8")
    resp5 = client.post(
        "/api/whatsapp/webhook",
        content=body5,
        headers={"X-Hub-Signature-256": _calcular_firma(body5), "Content-Type": "application/json"},
    )
    assert resp5.status_code == 200


def test_s3_resumen_salientes_calculo(db_session):
    """S3 resumen_salientes(db, dias=7, ahora=22/10/2026 15:00 de Argentina) con estas filas (horas de Argentina):
    R1 22/10 10:00 respuesta enviada, delivered, free_customer_service, entrante e1;
    R2 22/10 10:05 respuesta, delivered, free_customer_service, e2;
    R3 22/10 10:10 respuesta, read, regular, e3;
    R4 22/10 09:00 plantilla:resumen_ciclo (es_plantilla), delivered, regular;
    R5 22/10 11:00 respuesta NO enviada, motivo tope_diario_salientes;
    R6 21/10 10:00 respuesta enviada sin estado, e6;
    R7 21/10 10:30 respuesta enviada sin estado, e7;
    R8 30/09 10:00 respuesta enviada, delivered, regular, e8;
    R9 22/10 12:00 respuesta enviada sin estado, e3.
    Esperado:
    mes enviados 7, plantillas 1, cobrados_meta 2, gratis_meta 2, sin_dato_meta 3, cupo_gratis 1000, cupo_usado 3, no_enviados 1;
    por_dia [21/10: total 2, respuestas 2, plantillas 0, otros 0; 22/10: total 5, respuestas 4, plantillas 1, otros 0];
    no_enviados_por_motivo {"tope_diario_salientes": 1};
    entrantes_con_mas_de_una_respuesta 1."""
    ahora = datetime(2026, 10, 22, 15, 0, 0, tzinfo=TZ_ARGENTINA)

    def f_ar(dia: int, mes: int, h: int, m: int) -> datetime:
        return datetime(2026, mes, dia, h, m, 0, tzinfo=TZ_ARGENTINA)

    filas = [
        # R1 22/10 10:00
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(22, 10, 10, 0), tipo="respuesta", es_plantilla=False, enviado=True, estado_meta="delivered", tipo_cobro="free_customer_service", wamid_entrante="e1"),
        # R2 22/10 10:05
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(22, 10, 10, 5), tipo="respuesta", es_plantilla=False, enviado=True, estado_meta="delivered", tipo_cobro="free_customer_service", wamid_entrante="e2"),
        # R3 22/10 10:10
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(22, 10, 10, 10), tipo="respuesta", es_plantilla=False, enviado=True, estado_meta="read", tipo_cobro="regular", wamid_entrante="e3"),
        # R4 22/10 09:00
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(22, 10, 9, 0), tipo="plantilla:resumen_ciclo", es_plantilla=True, enviado=True, estado_meta="delivered", tipo_cobro="regular", wamid_entrante=None),
        # R5 22/10 11:00
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(22, 10, 11, 0), tipo="respuesta", es_plantilla=False, enviado=False, motivo_no_enviado="tope_diario_salientes", wamid_entrante=None),
        # R6 21/10 10:00
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(21, 10, 10, 0), tipo="respuesta", es_plantilla=False, enviado=True, estado_meta=None, tipo_cobro=None, wamid_entrante="e6"),
        # R7 21/10 10:30
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(21, 10, 10, 30), tipo="respuesta", es_plantilla=False, enviado=True, estado_meta=None, tipo_cobro=None, wamid_entrante="e7"),
        # R8 30/09 10:00 (mes anterior)
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(30, 9, 10, 0), tipo="respuesta", es_plantilla=False, enviado=True, estado_meta="delivered", tipo_cobro="regular", wamid_entrante="e8"),
        # R9 22/10 12:00
        MensajeSalienteWpp(id=uuid4(), fecha=f_ar(22, 10, 12, 0), tipo="respuesta", es_plantilla=False, enviado=True, estado_meta=None, tipo_cobro=None, wamid_entrante="e3"),
    ]
    db_session.add_all(filas)
    db_session.commit()

    res = resumen_salientes(db_session, dias=7, ahora=ahora)

    mes = res["mes"]
    assert mes["enviados"] == 7
    assert mes["plantillas"] == 1
    assert mes["cobrados_meta"] == 2
    assert mes["gratis_meta"] == 2
    assert mes["sin_dato_meta"] == 3
    assert mes["cupo_gratis"] == 1000
    assert mes["cupo_usado"] == 3
    assert mes["no_enviados"] == 1

    por_dia = res["por_dia"]
    assert len(por_dia) == 2
    assert por_dia[0] == {"fecha": "21/10", "total": 2, "respuestas": 2, "plantillas": 0, "otros": 0}
    assert por_dia[1] == {"fecha": "22/10", "total": 5, "respuestas": 4, "plantillas": 1, "otros": 0}

    assert res["no_enviados_por_motivo"] == {"tope_diario_salientes": 1}
    assert res["entrantes_con_mas_de_una_respuesta"] == 1


def test_s4_endpoint_monitor_admin_trae_whatsapp_salientes(db_session):
    """S4 La respuesta de GET /v1/admin/monitor-wpp/resumen trae whatsapp_salientes con esas claves."""
    admin = _crear_usuario(db_session, rol=RolUsuario.ADMIN)
    fastapi_app.dependency_overrides[get_current_admin_user] = lambda: admin
    fastapi_app.dependency_overrides[get_current_user] = lambda: admin

    client = TestClient(fastapi_app)
    resp = client.get("/v1/admin/monitor-wpp/resumen?dias=7")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()

    assert "whatsapp_salientes" in data
    ws = data["whatsapp_salientes"]
    assert "mes" in ws
    assert "por_dia" in ws
    assert "no_enviados_por_motivo" in ws
    assert "entrantes_con_mas_de_una_respuesta" in ws

    mes = ws["mes"]
    assert "enviados" in mes
    assert "plantillas" in mes
    assert "cobrados_meta" in mes
    assert "gratis_meta" in mes
    assert "sin_dato_meta" in mes
    assert "cupo_gratis" in mes
    assert "cupo_usado" in mes
    assert "no_enviados" in mes


def test_s5_eliminar_usuario_anonimiza_salientes(db_session):
    """S5 eliminar_usuario deja usuario_id y telefono_hash en NULL en sus filas de mensajes_salientes_wpp."""
    usuario = _crear_usuario(db_session)
    tel_hash = hash_telefono(usuario.telefono)

    m = MensajeSalienteWpp(
        id=uuid4(),
        usuario_id=usuario.id,
        telefono_hash=tel_hash,
        tipo="respuesta",
        enviado=True,
    )
    db_session.add(m)
    db_session.commit()

    eliminar_usuario(db_session, usuario)

    db_session.refresh(m)
    assert m.usuario_id is None
    assert m.telefono_hash is None


def test_s6_purgar_salientes_antiguos(db_session):
    """S6 purgar_salientes_antiguos borra una fila de hace 401 días y deja una de hace 399."""
    ahora = datetime.now(timezone.utc)
    hace_401 = ahora - timedelta(days=401)
    hace_399 = ahora - timedelta(days=399)

    id_viejo = uuid4()
    id_reciente = uuid4()
    m_viejo = MensajeSalienteWpp(id=id_viejo, fecha=hace_401, tipo="respuesta", enviado=True)
    m_reciente = MensajeSalienteWpp(id=id_reciente, fecha=hace_399, tipo="respuesta", enviado=True)
    db_session.add_all([m_viejo, m_reciente])
    db_session.commit()

    borrados = purgar_salientes_antiguos(db_session)
    assert borrados >= 1

    restantes = db_session.scalars(select(MensajeSalienteWpp.id)).all()
    assert id_viejo not in restantes
    assert id_reciente in restantes
