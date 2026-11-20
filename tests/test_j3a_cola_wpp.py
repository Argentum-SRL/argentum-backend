# -*- coding: utf-8 -*-
"""
tests/test_j3a_cola_wpp.py — Pruebas de cola durable de WhatsApp, grupo de 8 hilos,
job rescatar_mensajes_wpp, migración f4a5b6c7d8e9 y purga de mensajes procesados.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import inspect
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

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
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.jobs_catalogo import JOBS_CATALOGO, JOBS_MANUALES_PERMITIDOS
from app.main import app
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.routers import whatsapp_ia
from app.routers.whatsapp_ia import (
    _preprocesar_webhook_whatsapp_sync,
    _procesar_desde_cola,
    whatsapp_webhook,
)
from app.services import cola_wpp_service
from app.services.rate_limit_service import purgar_mensajes_whatsapp_procesados_antiguos

AHORA = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
TEST_APP_SECRET = "test_app_secret_j3a_1234567890"
TEST_VERIFY_TOKEN = "test_verify_token_j3a"


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
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    monkeypatch.setattr("app.routers.whatsapp_ia.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.whatsapp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.salientes_wpp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.rate_limit_service.SessionLocal", TestingSession)
    monkeypatch.setattr(settings, "WHATSAPP_APP_SECRET", TEST_APP_SECRET)
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", TEST_VERIFY_TOKEN)
    monkeypatch.setattr("app.services.whatsapp_service.enviar_whatsapp", MagicMock(return_value=True))
    monkeypatch.setattr("app.services.whatsapp_service.marcar_leido_y_escribiendo", MagicMock(return_value=True))
    monkeypatch.setattr("app.routers.whatsapp_ia.marcar_leido_y_escribiendo", MagicMock(return_value=True))

    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        app.dependency_overrides.pop(get_db, None)


def _payload_meta_texto(wamid: str, from_number: str, body: str) -> bytes:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": from_number,
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")


def _firma_meta(body_bytes: bytes) -> str:
    sig = hmac.new(TEST_APP_SECRET.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def test_q1_preprocesar_guarda_pendiente_y_payload(db_session: Session):
    body = _payload_meta_texto("wamid.Q1", "5491100000001", "hola")
    resp, datos = _preprocesar_webhook_whatsapp_sync(body, 0.0)

    assert resp.status_code == 200
    assert datos is not None
    assert datos["wamid"] == "wamid.Q1"

    fila = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.Q1")
    ).scalar_one()
    assert fila.estado == "pendiente"
    assert fila.intentos == 0
    assert fila.tomado_en is None
    assert fila.payload == {
        "msg": datos["msg"],
        "from_number": "5491100000001",
        "msg_type": "text",
    }


def test_q2_q3_procesar_desde_cola_y_no_repite(db_session: Session, monkeypatch):
    body = _payload_meta_texto("wamid.Q2", "5491100000002", "gaste 100")
    _, datos = _preprocesar_webhook_whatsapp_sync(body, 0.0)
    assert datos is not None

    llamadas = []
    monkeypatch.setattr(
        whatsapp_ia,
        "_procesar_mensaje_whatsapp_background",
        lambda d: llamadas.append(d),
    )

    # Q2: primera llamada procesa y deja listo con payload None
    _procesar_desde_cola(datos)
    assert len(llamadas) == 1
    assert llamadas[0] == datos

    db_session.expire_all()
    fila = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.Q2")
    ).scalar_one()
    assert fila.estado == "listo"
    assert fila.payload is None
    assert fila.intentos == 1
    assert fila.tomado_en is not None

    # Q3: segunda llamada con el mismo wamid no vuelve a procesar
    _procesar_desde_cola(datos)
    assert len(llamadas) == 1
    db_session.expire_all()
    fila2 = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.Q2")
    ).scalar_one()
    assert fila2.estado == "listo"
    assert fila2.intentos == 1


def test_q4_procesar_desde_cola_falla_cierra_como_fallo(db_session: Session, monkeypatch):
    body = _payload_meta_texto("wamid.Q4", "5491100000004", "hola")
    _, datos = _preprocesar_webhook_whatsapp_sync(body, 0.0)
    assert datos is not None

    def _explota(_datos):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(whatsapp_ia, "_procesar_mensaje_whatsapp_background", _explota)

    _procesar_desde_cola(datos)  # No relanza

    db_session.expire_all()
    fila = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.Q4")
    ).scalar_one()
    assert fila.estado == "fallo"
    assert fila.payload is None
    assert fila.intentos == 1


def test_q5_datos_sin_wamid_procesa_sin_tocar_cola(db_session: Session, monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        whatsapp_ia,
        "_procesar_mensaje_whatsapp_background",
        lambda d: llamadas.append(d),
    )

    datos = {
        "msg": {"from": "5491100000005", "type": "text", "text": {"body": "hola"}},
        "wamid": None,
        "from_number": "5491100000005",
        "msg_type": "text",
        "t_inicio": None,
    }
    _procesar_desde_cola(datos)

    assert len(llamadas) == 1
    assert llamadas[0] == datos
    total = db_session.execute(select(MensajeWhatsappProcesado)).scalars().all()
    assert len(total) == 0


def _poblar_escenario_rescate(db: Session) -> dict[str, dict]:
    p_a1 = {"msg": {"id": "wamid.A1", "from": "5491100000011", "type": "text", "text": {"body": "a1"}}, "from_number": "5491100000011", "msg_type": "text"}
    p_a2 = {"msg": {"id": "wamid.A2", "from": "5491100000012", "type": "text", "text": {"body": "a2"}}, "from_number": "5491100000012", "msg_type": "text"}
    p_b = {"msg": {"id": "wamid.B", "from": "5491100000013", "type": "text", "text": {"body": "b"}}, "from_number": "5491100000013", "msg_type": "text"}
    p_c = {"msg": {"id": "wamid.C", "from": "5491100000014", "type": "text", "text": {"body": "c"}}, "from_number": "5491100000014", "msg_type": "text"}
    p_d = {"msg": {"id": "wamid.D", "from": "5491100000015", "type": "text", "text": {"body": "d"}}, "from_number": "5491100000015", "msg_type": "text"}
    p_e = {"msg": {"id": "wamid.E", "from": "5491100000016", "type": "text", "text": {"body": "e"}}, "from_number": "5491100000016", "msg_type": "text"}

    filas = [
        MensajeWhatsappProcesado(wamid="wamid.A1", telefono="5491100000011", tipo_mensaje="text", estado="pendiente", payload=p_a1, intentos=0, fecha_recepcion=AHORA - timedelta(minutes=3)),
        MensajeWhatsappProcesado(wamid="wamid.A2", telefono="5491100000012", tipo_mensaje="text", estado="pendiente", payload=p_a2, intentos=0, fecha_recepcion=AHORA - timedelta(minutes=2)),
        MensajeWhatsappProcesado(wamid="wamid.B", telefono="5491100000013", tipo_mensaje="text", estado="pendiente", payload=p_b, intentos=0, fecha_recepcion=AHORA - timedelta(seconds=30)),
        MensajeWhatsappProcesado(wamid="wamid.C", telefono="5491100000014", tipo_mensaje="text", estado="pendiente", payload=p_c, intentos=0, fecha_recepcion=AHORA - timedelta(minutes=61)),
        MensajeWhatsappProcesado(wamid="wamid.D", telefono="5491100000015", tipo_mensaje="text", estado="procesando", payload=p_d, intentos=1, fecha_recepcion=AHORA - timedelta(minutes=12), tomado_en=AHORA - timedelta(minutes=11)),
        MensajeWhatsappProcesado(wamid="wamid.E", telefono="5491100000016", tipo_mensaje="text", estado="procesando", payload=p_e, intentos=1, fecha_recepcion=AHORA - timedelta(minutes=6), tomado_en=AHORA - timedelta(minutes=5)),
        MensajeWhatsappProcesado(wamid="wamid.F", telefono="5491100000017", tipo_mensaje="text", estado="listo", payload=None, intentos=1, fecha_recepcion=AHORA - timedelta(minutes=5)),
        MensajeWhatsappProcesado(wamid="wamid.G", telefono="5491100000018", tipo_mensaje="text", estado="pendiente", payload=None, intentos=0, fecha_recepcion=AHORA - timedelta(minutes=2)),
    ]
    db.add_all(filas)
    db.commit()
    return {"A1": p_a1, "A2": p_a2, "B": p_b}


def test_q6_rescatar_pendientes_completo(db_session: Session):
    payloads = _poblar_escenario_rescate(db_session)

    recibidos = []
    res = cola_wpp_service.rescatar_pendientes(
        db_session,
        procesar=lambda d: recibidos.append(d),
        ahora=AHORA,
    )

    assert res == {"rescatados": 2, "fallados": 0, "vencidos": 1, "interrumpidos": 1}
    assert recibidos == [
        cola_wpp_service.datos_desde_fila("wamid.A1", payloads["A1"]),
        cola_wpp_service.datos_desde_fila("wamid.A2", payloads["A2"]),
    ]

    db_session.expire_all()
    por_wamid = {
        f.wamid: f
        for f in db_session.execute(select(MensajeWhatsappProcesado)).scalars().all()
    }
    assert por_wamid["wamid.A1"].estado == "listo"
    assert por_wamid["wamid.A1"].payload is None
    assert por_wamid["wamid.A1"].intentos == 1

    assert por_wamid["wamid.A2"].estado == "listo"
    assert por_wamid["wamid.A2"].payload is None
    assert por_wamid["wamid.A2"].intentos == 1

    assert por_wamid["wamid.B"].estado == "pendiente"
    assert por_wamid["wamid.B"].payload == payloads["B"]

    assert por_wamid["wamid.C"].estado == "vencido"
    assert por_wamid["wamid.C"].payload is None

    assert por_wamid["wamid.D"].estado == "interrumpido"
    assert por_wamid["wamid.D"].payload is None

    assert por_wamid["wamid.E"].estado == "procesando"
    assert por_wamid["wamid.F"].estado == "listo"
    assert por_wamid["wamid.G"].estado == "pendiente"


def test_q7_rescatar_pendientes_uno_falla_sigue_otro(db_session: Session):
    _poblar_escenario_rescate(db_session)

    def _procesar(datos: dict) -> None:
        if datos["wamid"] == "wamid.A1":
            raise RuntimeError("error en A1")

    res = cola_wpp_service.rescatar_pendientes(db_session, procesar=_procesar, ahora=AHORA)
    assert res == {"rescatados": 1, "fallados": 1, "vencidos": 1, "interrumpidos": 1}

    db_session.expire_all()
    a1 = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.A1")
    ).scalar_one()
    a2 = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.A2")
    ).scalar_one()
    assert a1.estado == "fallo"
    assert a1.payload is None
    assert a2.estado == "listo"
    assert a2.payload is None


def test_q8_tomar_mensaje_atomico(db_session: Session):
    t_orig = AHORA - timedelta(minutes=3)
    f_proc = MensajeWhatsappProcesado(
        wamid="wamid.Q8.proc",
        telefono="5491100000020",
        tipo_mensaje="text",
        estado="procesando",
        intentos=1,
        tomado_en=t_orig,
    )
    f_pend = MensajeWhatsappProcesado(
        wamid="wamid.Q8.pend",
        telefono="5491100000021",
        tipo_mensaje="text",
        estado="pendiente",
        intentos=0,
    )
    db_session.add_all([f_proc, f_pend])
    db_session.commit()

    assert cola_wpp_service.tomar_mensaje(db_session, "wamid.Q8.proc", AHORA) is False
    db_session.expire_all()
    f_proc_desp = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.Q8.proc")
    ).scalar_one()
    assert f_proc_desp.estado == "procesando"
    assert f_proc_desp.intentos == 1

    assert cola_wpp_service.tomar_mensaje(db_session, "wamid.Q8.pend", AHORA) is True
    assert cola_wpp_service.tomar_mensaje(db_session, "wamid.Q8.pend", AHORA) is False
    db_session.expire_all()
    f_pend_desp = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == "wamid.Q8.pend")
    ).scalar_one()
    assert f_pend_desp.estado == "procesando"
    assert f_pend_desp.intentos == 1


def test_q9_cerrar_mensaje_no_lanza_y_hace_rollback():
    mock_db = MagicMock()
    mock_db.commit.side_effect = RuntimeError("db caida")

    cola_wpp_service.cerrar_mensaje(mock_db, "wamid.Q9", cola_wpp_service.LISTO)

    assert mock_db.rollback.call_count == 1


def test_q10_webhook_http_pool_wpp_y_dedup(db_session: Session, monkeypatch):
    registros = []

    def _registro(datos: dict) -> None:
        registros.append((datos["wamid"], threading.current_thread().name))

    monkeypatch.setattr(whatsapp_ia, "_procesar_mensaje_whatsapp_background", _registro)

    wamid = "wamid.Q10.http"
    body = _payload_meta_texto(wamid, "5491100000030", "cafe 2000")
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _firma_meta(body),
    }

    client = TestClient(app)
    r1 = client.post("/api/whatsapp/webhook", content=body, headers=headers)
    assert r1.status_code == 200
    assert r1.text == "OK"

    assert len(registros) == 1
    assert registros[0][0] == wamid
    assert registros[0][1].startswith("wpp")

    db_session.expire_all()
    fila = db_session.execute(
        select(MensajeWhatsappProcesado).where(MensajeWhatsappProcesado.wamid == wamid)
    ).scalar_one()
    assert fila.estado == "listo"
    assert fila.payload is None

    r2 = client.post("/api/whatsapp/webhook", content=body, headers=headers)
    assert r2.status_code == 200
    assert len(registros) == 1


def test_q11_pool_wpp_8_hilos():
    assert cola_wpp_service.HILOS_WPP == 8
    assert cola_wpp_service.POOL_WPP._max_workers == 8


def test_q12_estaticos_cola_wpp():
    backend_dir = Path(__file__).resolve().parent.parent
    whatsapp_ia_src = (backend_dir / "app" / "routers" / "whatsapp_ia.py").read_text(encoding="utf-8")
    assert "background_tasks.add_task(_procesar_mensaje_whatsapp_background" not in whatsapp_ia_src

    webhook_src = inspect.getsource(whatsapp_webhook)
    assert "ejecutar_en_pool_wpp" in webhook_src
    assert "_procesar_desde_cola" in webhook_src

    cola_path = backend_dir / "app" / "services" / "cola_wpp_service.py"
    cola_src = cola_path.read_text(encoding="utf-8")
    tree = ast.parse(cola_src, filename=str(cola_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.routers")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith("app.routers")

    rescatar_src = inspect.getsource(cola_wpp_service.rescatar_pendientes)
    assert "select(" in rescatar_src
    consulta_candidatos = rescatar_src.split("select(", 1)[1].split("LOTE_RESCATE", 1)[0]
    assert "INTERRUMPIDO" not in consulta_candidatos
    assert "PROCESANDO" not in consulta_candidatos
    assert "limite_atraso" in consulta_candidatos or "MAXIMO_ATRASO" in consulta_candidatos


def test_j1_jobs_catalogo_rescatar_mensajes_wpp():
    assert len(JOBS_CATALOGO) == 25
    ids = [j.job_id for j in JOBS_CATALOGO]
    idx_reintento = ids.index("reintentar_avisos_inmediatos")
    assert ids[idx_reintento + 1] == "rescatar_mensajes_wpp"

    item = JOBS_CATALOGO[idx_reintento + 1]
    assert item.nombre == "Mensajes de WhatsApp sin responder"
    assert item.que_hace == (
        "Responde los mensajes de WhatsApp que llegaron justo cuando el servidor se reiniciaba "
        "(hasta 1 hora de atraso)."
    )
    assert item.periodo_esperado_minutos == 2
    assert item.puede_correr_ahora is False
    assert "rescatar_mensajes_wpp" not in JOBS_MANUALES_PERMITIDOS


def test_j2_lifespan_registra_rescatar_mensajes_wpp():
    src = inspect.getsource(app_main.lifespan)
    assert 'id="rescatar_mensajes_wpp"' in src
    bloque = src.split('id="rescatar_mensajes_wpp"', 1)[0].rsplit("scheduler.add_job(", 1)[1]
    assert '"interval"' in bloque
    assert "minutes=2" in bloque


def test_j3_job_rescatar_mensajes_wpp_relanza_y_respeta_lock():
    mock_db = MagicMock()
    locks_liberados = []

    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job", side_effect=lambda db, name: locks_liberados.append(name)), \
         patch("app.main.cola_wpp_service.rescatar_pendientes", side_effect=RuntimeError("falla simulada")):
        with pytest.raises(RuntimeError, match="falla simulada"):
            app_main._job_rescatar_mensajes_wpp()

    assert "_job_rescatar_mensajes_wpp" in locks_liberados
    assert mock_db.close.call_count == 1

    mock_db2 = MagicMock()
    with patch("app.main.SessionLocal", return_value=mock_db2), \
         patch("app.main.intentar_tomar_lock_job", return_value=False), \
         patch("app.main.liberar_lock_job") as mock_lib, \
         patch("app.main.cola_wpp_service.rescatar_pendientes") as mock_rescatar:
        app_main._job_rescatar_mensajes_wpp()

    mock_rescatar.assert_not_called()
    mock_lib.assert_not_called()
    assert mock_db2.close.call_count == 1


def test_j4_job_rescatar_mensajes_wpp_pasa_procesar_correcto():
    mock_db = MagicMock()
    llamado_con = {}

    def _fake_rescatar(db, *, procesar, ahora):
        llamado_con["db"] = db
        llamado_con["procesar"] = procesar
        llamado_con["ahora"] = ahora
        return {"rescatados": 0, "fallados": 0, "vencidos": 0, "interrumpidos": 0}

    with patch("app.main.SessionLocal", return_value=mock_db), \
         patch("app.main.intentar_tomar_lock_job", return_value=True), \
         patch("app.main.liberar_lock_job"), \
         patch("app.main.cola_wpp_service.rescatar_pendientes", side_effect=_fake_rescatar):
        app_main._job_rescatar_mensajes_wpp()

    assert llamado_con["db"] is mock_db
    assert llamado_con["procesar"] is whatsapp_ia._procesar_mensaje_whatsapp_background


def test_m1_migracion_f4a5b6c7d8e9():
    backend_dir = Path(__file__).resolve().parent.parent
    mig_path = backend_dir / "alembic" / "versions" / "f4a5b6c7d8e9_cola_wpp.py"
    assert mig_path.exists()
    txt = mig_path.read_text(encoding="utf-8")

    assert '"f4a5b6c7d8e9"' in txt
    assert '"d5e6f7a8b9c1"' in txt
    assert '"estado"' in txt
    assert '"payload"' in txt
    assert '"intentos"' in txt
    assert '"tomado_en"' in txt
    assert 'server_default="listo"' in txt
    assert 'server_default="0"' in txt
    assert "ix_mensajes_wpp_procesados_estado_fecha" in txt
    assert "def downgrade()" in txt
    down_part = txt.split("def downgrade()", 1)[1]
    assert "drop_index" in down_part
    assert "ix_mensajes_wpp_procesados_estado_fecha" in down_part
    for col in ("estado", "payload", "intentos", "tomado_en"):
        assert f'drop_column("mensajes_whatsapp_procesados", "{col}")' in down_part


def test_p1_purgar_mensajes_whatsapp_procesados_antiguos(db_session: Session):
    ahora_real = datetime.now(timezone.utc)
    viejo = MensajeWhatsappProcesado(
        wamid="wamid.P1.viejo",
        telefono="5491100000091",
        tipo_mensaje="text",
        estado="listo",
        fecha_recepcion=ahora_real - timedelta(days=8),
    )
    reciente = MensajeWhatsappProcesado(
        wamid="wamid.P1.reciente",
        telefono="5491100000092",
        tipo_mensaje="text",
        estado="listo",
        fecha_recepcion=ahora_real - timedelta(days=6),
    )
    db_session.add_all([viejo, reciente])
    db_session.commit()

    borrados = purgar_mensajes_whatsapp_procesados_antiguos(db_session)
    assert borrados == 1

    restantes = db_session.execute(select(MensajeWhatsappProcesado.wamid)).scalars().all()
    assert restantes == ["wamid.P1.reciente"]
