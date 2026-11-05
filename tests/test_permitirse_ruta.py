# -*- coding: utf-8 -*-
"""
tests/test_permitirse_ruta.py — Pruebas de integración de endpoints /tools/can-afford, /tools/inflacion-esperada y paridad con WhatsApp.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.handlers_permitirse import (
    armar_respuesta_permitirse,
    detectar_consulta_permitirse,
    manejar_consulta_permitirse,
)
from app.services.permitirse_service import calcular_puede_permitirse


# Setup SQLite compatibility para pruebas unitarias
@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid_processor = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="usuario_caja_270k")
def usuario_caja_270k_fixture(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email="sin_ingreso_caja@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        telefono="+5491100000000",
    )
    db.add(u)
    db.flush()

    caja = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("270000"),
        saldo_inicial=Decimal("270000"),
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
    )
    db.add(caja)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_caja_270k: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_caja_270k

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_permitirse_ruta_contado_1000(client: TestClient):
    # POST /tools/can-afford {"precio_total": 1000, "modo": "contado"}
    # → 200, resultado "justo", motivo "sin_ingreso", titulo "Te entra justo",
    # linea_principal "Hoy te quedarían $269.000 libres, pero todavía no conozco bien tus meses.", ahorros null
    resp = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "contado"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["resultado"] == "justo"
    assert data["motivo"] == "sin_ingreso"
    assert data["titulo"] == "Te entra justo"
    assert data["linea_principal"] == "Hoy te quedar\u00edan $269.000 libres, pero todav\u00eda no conozco bien tus meses."
    assert data["ahorros"] is None


def test_permitirse_ruta_mensual_1000(client: TestClient):
    # {"precio_total": 1000, "modo": "mensual"}
    # → resultado "justo", linea_principal "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."
    resp = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "mensual"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["resultado"] == "justo"
    assert data["linea_principal"] == "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."


def test_permitirse_ruta_ingreso_manual_ignorado(client: TestClient):
    # con "ingreso_manual": 5 → misma respuesta
    resp = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "mensual", "ingreso_manual": 5})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["resultado"] == "justo"
    assert data["linea_principal"] == "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."


def test_permitirse_ruta_no_alcanza_300000(client: TestClient):
    # {"precio_total": 300000, "modo": "contado"} → "no_alcanza", linea_principal "Te faltan $30.000."
    resp = client.post("/tools/can-afford", json={"precio_total": 300000, "modo": "contado"})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["resultado"] == "no_alcanza"
    assert data["linea_principal"] == "Te faltan $30.000."


def test_permitirse_ruta_modo_invalido(client: TestClient):
    # modo "otro" → 422
    resp = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "otro"})
    assert resp.status_code == 422


def test_inflacion_esperada_sin_datos(client: TestClient):
    # GET /tools/inflacion-esperada sin datos de REM ni IPC → 200 con data null
    resp = client.get("/tools/inflacion-esperada")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"] is None


def test_paridad_ruta_whatsapp(db: Session, usuario_caja_270k: Usuario, client: TestClient):
    # La respuesta de WhatsApp de manejar_consulta_permitirse para "¿me puedo permitir algo de 1000?"
    # con ese usuario == armar_respuesta_permitirse(calcular_puede_permitirse(...)) y su segunda línea == linea_principal de la ruta (paridad).
    texto = "¿me puedo permitir algo de 1000?"
    consulta = detectar_consulta_permitirse(texto)
    assert consulta is not None

    res_motor = calcular_puede_permitirse(
        db=db,
        user_id=usuario_caja_270k.id,
        precio_total=consulta["precio"],
        modo=consulta["modo"],
        cantidad_cuotas=consulta["cantidad_cuotas"],
        tiene_interes=consulta["tiene_interes"],
        tna=consulta["tna"],
        ingreso_manual=None,
    )
    esp_wpp = armar_respuesta_permitirse(res_motor, consulta)

    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={},
        wamid="wamid-test-paridad",
        from_number="+5491100000000",
        msg_type="text",
        t_inicio=0.0,
        db=db,
        usuario=usuario_caja_270k,
        mensaje_texto=texto,
    )

    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
        manejado = manejar_consulta_permitirse(ctx)
        assert manejado is True
        mock_envio.assert_called_once()
        texto_enviado = mock_envio.call_args[0][1]

    # Paridad completa
    assert texto_enviado == esp_wpp

    # Segunda línea == linea_principal de la ruta
    resp_ruta = client.post("/tools/can-afford", json={"precio_total": 1000, "modo": "contado"})
    assert resp_ruta.status_code == 200
    linea_principal_ruta = resp_ruta.json()["data"]["linea_principal"]

    lineas_wpp = texto_enviado.split("\n")
    assert len(lineas_wpp) >= 2
    assert lineas_wpp[1] == linea_principal_ruta
