# -*- coding: utf-8 -*-
"""
tests/test_uso_eventos.py — Pruebas de registro de eventos de uso de la web.
"""
from __future__ import annotations

import uuid
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.evento_uso import EventoUso
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.uso_service import registrar_evento


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


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    usuario = Usuario(
        id=uuid4(),
        email="test_uso@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_test: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_test

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_registrar_evento_idempotente_mismo_dia(db: Session, usuario_test: Usuario):
    """
    registrar_evento dos veces el mismo día para el mismo usuario y evento → True y después False, 1 fila;
    otro evento el mismo día → True;
    otro usuario → True;
    "otro" → ValueError("Evento desconocido: otro").
    """
    # 1. Primera llamada -> True, 1 fila
    assert registrar_evento(db, usuario_test.id, "visita") is True
    filas = db.query(EventoUso).filter(EventoUso.usuario_id == usuario_test.id, EventoUso.evento == "visita").all()
    assert len(filas) == 1

    # 2. Segunda llamada mismo día y usuario -> False, sigue habiendo 1 fila
    assert registrar_evento(db, usuario_test.id, "visita") is False
    filas = db.query(EventoUso).filter(EventoUso.usuario_id == usuario_test.id, EventoUso.evento == "visita").all()
    assert len(filas) == 1

    # 3. Otro evento el mismo día -> True
    assert registrar_evento(db, usuario_test.id, "permitirse") is True
    filas_perm = db.query(EventoUso).filter(EventoUso.usuario_id == usuario_test.id, EventoUso.evento == "permitirse").all()
    assert len(filas_perm) == 1

    # 4. Otro usuario el mismo día -> True
    otro_id = uuid4()
    assert registrar_evento(db, otro_id, "visita") is True
    filas_otro = db.query(EventoUso).filter(EventoUso.usuario_id == otro_id, EventoUso.evento == "visita").all()
    assert len(filas_otro) == 1

    # 5. Evento desconocido -> ValueError
    with pytest.raises(ValueError, match="Evento desconocido: otro"):
        registrar_evento(db, usuario_test.id, "otro")


def test_post_uso_visita_dos_veces(client: TestClient, db: Session, usuario_test: Usuario):
    """
    POST /uso/visita dos veces → 204 y 204, 1 fila "visita".
    """
    resp1 = client.post("/uso/visita")
    assert resp1.status_code == 204

    resp2 = client.post("/uso/visita")
    assert resp2.status_code == 204

    filas = db.query(EventoUso).filter(EventoUso.usuario_id == usuario_test.id, EventoUso.evento == "visita").all()
    assert len(filas) == 1


def test_post_tools_endpoints_y_eventos(client: TestClient, db: Session, usuario_test: Usuario):
    """
    POST a can-afford y a installment-convenience con un cuerpo válido tomado de los tests existentes de esas rutas
    → la misma respuesta que antes del paso y 1 fila "permitirse" y 1 "calculadora_cuotas".
    """
    # 1. installment-convenience
    body_cuotas = {
        "precio_contado": 100000.0,
        "cantidad_cuotas": 6,
        "tiene_interes": False,
        "precio_total_cuotas": 100000.0,
        "inflacion_mensual": 0.0,
        "tna_billetera_inversion": 60.0,
    }
    resp_cuotas = client.post("/api/v1/tools/installment-convenience", json=body_cuotas)
    assert resp_cuotas.status_code == 200
    json_cuotas = resp_cuotas.json()
    assert json_cuotas["success"] is True
    assert json_cuotas["data"]["resultado"] == "conviene_cuotas"

    filas_cuotas = db.query(EventoUso).filter(
        EventoUso.usuario_id == usuario_test.id,
        EventoUso.evento == "calculadora_cuotas",
    ).all()
    assert len(filas_cuotas) == 1

    # 2. can-afford
    body_can_afford = {
        "precio_total": 50000.0,
        "modo": "contado",
        "cantidad_cuotas": 1,
        "tiene_interes": False,
    }
    resp_afford = client.post("/api/v1/tools/can-afford", json=body_can_afford)
    assert resp_afford.status_code == 200
    json_afford = resp_afford.json()
    assert json_afford["success"] is True
    assert "semaforo" in json_afford["data"]

    filas_afford = db.query(EventoUso).filter(
        EventoUso.usuario_id == usuario_test.id,
        EventoUso.evento == "permitirse",
    ).all()
    assert len(filas_afford) == 1


def test_can_afford_sigue_200_si_flush_de_evento_falla(client: TestClient, monkeypatch):
    """
    Con registrar_evento forzado a fallar en el flush (monkeypatch), can-afford sigue respondiendo 200.
    """
    from sqlalchemy.orm import Session as SASession

    def flush_falla(self, *args, **kwargs):
        # Provocar excepción deliberada durante el flush
        raise RuntimeError("Fallo forzado en db.flush para prueba de resiliencia")

    monkeypatch.setattr(SASession, "flush", flush_falla)

    body_can_afford = {
        "precio_total": 50000.0,
        "modo": "contado",
        "cantidad_cuotas": 1,
        "tiene_interes": False,
    }
    resp = client.post("/api/v1/tools/can-afford", json=body_can_afford)
    assert resp.status_code == 200
    assert resp.json()["success"] is True

