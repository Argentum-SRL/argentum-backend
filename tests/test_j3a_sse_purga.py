# -*- coding: utf-8 -*-
"""
tests/test_j3a_sse_purga.py — Pruebas de consulta SSE en hilo separado, cursores
sin pérdida de novedades, purga de eventos_actualizacion y catálogo de limpieza.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
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

sqlite3.register_adapter(UUID, lambda u: u.hex)

import app.models  # noqa: F401
import app.main as app_main
from app.core.auth import crear_access_token
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.jobs_catalogo import JOBS_CATALOGO
from app.core.limiter import limiter
from app.main import app
from app.models.evento_actualizacion import EventoActualizacion
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers import notificaciones as notificaciones_router
from app.routers.notificaciones import _leer_novedades_sse, sse_notificaciones
from app.services.evento_service import purgar_eventos_actualizacion

AHORA = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(settings, "SECRET_KEY", "clave_prueba_sse_j3a")
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    monkeypatch.setattr("app.routers.notificaciones.SessionLocal", TestingSession)

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        app.dependency_overrides.pop(get_db, None)


def _crear_usuario(db: Session, email: str | None = None) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email or f"sse_{uuid4().hex[:8]}@argentum.test",
        nombre="Test",
        apellido="SSE",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_e1_leer_novedades_sse_notificaciones_y_cursor(db_session: Session):
    u1 = _crear_usuario(db_session)
    u2 = _crear_usuario(db_session)

    n_antes = Notificacion(
        id=uuid4(),
        usuario_id=u1.id,
        tipo=TipoNotificacion.SALDO_CERO,
        nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
        mensaje="Anterior",
        leida=False,
        archivada=False,
        created_at=AHORA - timedelta(seconds=1),
    )
    n1 = Notificacion(
        id=uuid4(),
        usuario_id=u1.id,
        tipo=TipoNotificacion.SALDO_CERO,
        nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
        mensaje="Nueva 1",
        leida=False,
        archivada=False,
        created_at=AHORA + timedelta(seconds=1),
    )
    n2 = Notificacion(
        id=uuid4(),
        usuario_id=u1.id,
        tipo=TipoNotificacion.PRESUPUESTO_LIMITE,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje="Nueva 2",
        leida=False,
        archivada=False,
        created_at=AHORA + timedelta(seconds=2),
    )
    n_leida = Notificacion(
        id=uuid4(),
        usuario_id=u1.id,
        tipo=TipoNotificacion.SALDO_CERO,
        nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
        mensaje="Ya leida",
        leida=True,
        archivada=False,
        created_at=AHORA + timedelta(seconds=3),
    )
    n_silenciada = Notificacion(
        id=uuid4(),
        usuario_id=u1.id,
        tipo=TipoNotificacion.SALDO_CERO,
        nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
        mensaje="Silenciada",
        leida=False,
        archivada=False,
        silenciada_hasta=datetime(2099, 1, 1, 15, 0, tzinfo=timezone.utc),
        created_at=AHORA + timedelta(seconds=4),
    )
    n_otro = Notificacion(
        id=uuid4(),
        usuario_id=u2.id,
        tipo=TipoNotificacion.SALDO_CERO,
        nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
        mensaje="Otro usuario",
        leida=False,
        archivada=False,
        created_at=AHORA + timedelta(seconds=5),
    )
    db_session.add_all([n_antes, n1, n2, n_leida, n_silenciada, n_otro])
    db_session.commit()

    res = _leer_novedades_sse(u1.id, None, AHORA, AHORA)

    assert res["revocada"] is False
    assert [n["id"] for n in res["notificaciones"]] == [str(n1.id), str(n2.id)]
    claves_esperadas = {"id", "tipo", "nivel", "mensaje", "leida", "archivada", "deep_link", "created_at"}
    for n_dict in res["notificaciones"]:
        assert set(n_dict.keys()) == claves_esperadas
    assert res["desde_notif"] == AHORA + timedelta(seconds=2)
    assert res["desde_notif"].tzinfo == timezone.utc


def test_e2_leer_novedades_sse_eventos_y_cursor(db_session: Session):
    u1 = _crear_usuario(db_session)

    ev_antes = EventoActualizacion(
        usuario_id=u1.id,
        entidad="presupuestos",
        created_at=AHORA - timedelta(seconds=1),
    )
    ev1 = EventoActualizacion(
        usuario_id=u1.id,
        entidad="transacciones",
        created_at=AHORA + timedelta(seconds=1),
    )
    ev2 = EventoActualizacion(
        usuario_id=u1.id,
        entidad="billeteras",
        created_at=AHORA + timedelta(seconds=2),
    )
    db_session.add_all([ev_antes, ev1, ev2])
    db_session.commit()

    res = _leer_novedades_sse(u1.id, None, AHORA, AHORA)

    assert res["revocada"] is False
    assert res["entidades"] == ["transacciones", "billeteras"]
    assert res["desde_eventos"] == AHORA + timedelta(seconds=2)
    assert res["desde_eventos"].tzinfo == timezone.utc


def test_e3_leer_novedades_sse_sin_novedades_no_avanza_cursores(db_session: Session):
    u1 = _crear_usuario(db_session)
    desde_n = AHORA - timedelta(minutes=5)
    desde_e = AHORA - timedelta(minutes=3)

    res = _leer_novedades_sse(u1.id, None, desde_n, desde_e)

    assert res == {
        "revocada": False,
        "notificaciones": [],
        "entidades": [],
        "desde_notif": desde_n,
        "desde_eventos": desde_e,
    }


def test_e4_leer_novedades_sse_revocacion(db_session: Session):
    u1 = _crear_usuario(db_session)
    u1.tokens_revocados_at = AHORA
    db_session.commit()

    # iat_dt anterior a tokens_revocados_at -> revocada True
    res_rev = _leer_novedades_sse(u1.id, AHORA - timedelta(minutes=1), AHORA, AHORA)
    assert res_rev["revocada"] is True
    assert res_rev["notificaciones"] == []
    assert res_rev["entidades"] == []

    # usuario inexistente con iat_dt -> revocada True
    res_inex = _leer_novedades_sse(uuid4(), AHORA, AHORA, AHORA)
    assert res_inex["revocada"] is True
    assert res_inex["notificaciones"] == []
    assert res_inex["entidades"] == []

    # iat_dt None -> no revisa revocación
    res_sin_iat = _leer_novedades_sse(u1.id, None, AHORA, AHORA)
    assert res_sin_iat["revocada"] is False


def test_e5_leer_novedades_sse_error_db_no_lanza(monkeypatch):
    def _falla():
        raise RuntimeError("error de conexion simulado")

    monkeypatch.setattr(notificaciones_router, "SessionLocal", _falla)

    desde_n = AHORA
    desde_e = AHORA + timedelta(seconds=10)
    res = _leer_novedades_sse(uuid4(), None, desde_n, desde_e)

    assert res == {
        "revocada": False,
        "notificaciones": [],
        "entidades": [],
        "desde_notif": desde_n,
        "desde_eventos": desde_e,
    }


def test_e6_estaticos_sse_sin_db_en_event_loop():
    src_sse = inspect.getsource(sse_notificaciones)
    assert "anyio.to_thread.run_sync(autenticar_sse" in src_sse
    assert "anyio.to_thread.run_sync(_leer_novedades_sse" in src_sse
    assert "SessionLocal(" not in src_sse
    assert ".query(" not in src_sse
    assert src_sse.count("datetime.now(timezone.utc)") == 2
    assert "ultimo_check = datetime.now(timezone.utc)" in src_sse
    assert "ultimo_check_eventos = datetime.now(timezone.utc)" in src_sse

    src_leer = inspect.getsource(_leer_novedades_sse)
    assert "datetime.now(" not in src_leer


def test_e7_endpoint_sse_stream_connected_notif_revoked(db_session: Session, monkeypatch):
    usuario = _crear_usuario(db_session)
    token = crear_access_token(usuario.id)

    llamadas = {"n": 0}
    notif_ejemplo = {
        "id": str(uuid4()),
        "tipo": "SALDO_CERO",
        "nivel": "FINANCIERA_INFORMATIVA",
        "mensaje": "Aviso de prueba SSE",
        "leida": False,
        "archivada": False,
        "deep_link": None,
        "created_at": AHORA.isoformat(),
    }

    def _fake_leer(uid, iat_dt, desde_n, desde_e):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            return {
                "revocada": False,
                "notificaciones": [notif_ejemplo],
                "entidades": [],
                "desde_notif": AHORA + timedelta(seconds=1),
                "desde_eventos": desde_e,
            }
        return {
            "revocada": True,
            "notificaciones": [],
            "entidades": [],
            "desde_notif": desde_n,
            "desde_eventos": desde_e,
        }

    async def _sleep_cero(_seg):
        return None

    monkeypatch.setattr(notificaciones_router.asyncio, "sleep", _sleep_cero)
    monkeypatch.setattr(notificaciones_router, "_leer_novedades_sse", _fake_leer)

    client = TestClient(app)
    client.cookies.set("access_token", token)
    with client.stream("GET", "/notificaciones/sse") as resp:
        assert resp.status_code == 200
        lineas = [line for line in resp.iter_lines() if line.startswith("data: ")]

    assert len(lineas) == 3
    ev0 = json.loads(lineas[0].removeprefix("data: "))
    ev1 = json.loads(lineas[1].removeprefix("data: "))
    ev2 = json.loads(lineas[2].removeprefix("data: "))
    assert ev0 == {"event": "connected"}
    assert ev1 == notif_ejemplo
    assert ev2 == {"event": "revoked"}


def test_f1_purgar_eventos_actualizacion_1_dia(db_session: Session):
    u = _crear_usuario(db_session)
    ev_25h = EventoActualizacion(
        usuario_id=u.id,
        entidad="transacciones",
        created_at=AHORA - timedelta(hours=25),
    )
    ev_24h_1s = EventoActualizacion(
        usuario_id=u.id,
        entidad="billeteras",
        created_at=AHORA - timedelta(hours=24, seconds=1),
    )
    ev_23h = EventoActualizacion(
        usuario_id=u.id,
        entidad="tarjetas",
        created_at=AHORA - timedelta(hours=23),
    )
    db_session.add_all([ev_25h, ev_24h_1s, ev_23h])
    db_session.commit()

    borrados = purgar_eventos_actualizacion(db_session, ahora=AHORA)
    assert borrados == 2

    restantes = db_session.execute(select(EventoActualizacion.entidad)).scalars().all()
    assert restantes == ["tarjetas"]


def test_f2_job_limpiar_tokens_llama_purgar_eventos():
    mock_db = MagicMock()
    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job"), \
         patch("app.main.limpiar_tokens_expirados", return_value=0), \
         patch("app.main.limpiar_codigos_y_rate_limits_expirados", return_value={"codigos_verificacion_eliminados": 0, "rate_limits_eliminados": 0}), \
         patch("app.main.purgar_conversaciones_wpp_antiguas", return_value=0), \
         patch("app.main.purgar_mensajes_whatsapp_procesados_antiguos", return_value=0), \
         patch("app.main.purgar_uso_ia_antiguo", return_value=0), \
         patch("app.main.purgar_salientes_antiguos", return_value=0), \
         patch("app.main.purgar_claves_idempotencia", return_value=0), \
         patch("app.main.purgar_eventos_actualizacion", return_value=3) as mock_purgar_ev:
        app_main._job_limpiar_tokens()

    mock_purgar_ev.assert_called_once_with(mock_db)


def test_f3_que_hace_limpiar_refresh_tokens():
    item = next(j for j in JOBS_CATALOGO if j.job_id == "limpiar_refresh_tokens")
    assert item.que_hace == (
        "Elimina tokens vencidos, códigos de verificación, mensajes antiguos de WhatsApp, "
        "avisos de cambios de más de 1 día y registros de costo de la IA de más de 400 días."
    )
