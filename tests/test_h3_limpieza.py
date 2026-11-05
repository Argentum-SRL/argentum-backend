# -*- coding: utf-8 -*-
"""
tests/test_h3_limpieza.py — Pruebas de verificación de la limpieza de la proyección vieja (Fase H3).
"""
from __future__ import annotations

import importlib.util
import sqlite3
import uuid
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.core.jobs_catalogo import JOBS_CATALOGO
from app.main import app as fastapi_app
import app.main as app_main
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.notificacion import TipoNotificacion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import notificacion_scheduler_service
from app.services.notificacion_service import resolver_canales_notificacion


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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


def test_1_modulos_eliminados_no_importables():
    """(1) importlib.util.find_spec da None para los 3 módulos borrados."""
    assert importlib.util.find_spec("app.services.proyeccion_service") is None
    assert importlib.util.find_spec("app.services.calibracion_service") is None
    assert importlib.util.find_spec("app.models.calibracion_usuario") is None


def test_2_tabla_calibraciones_usuario_no_en_metadata():
    """(2) 'calibraciones_usuario' no está en Base.metadata.tables tras importar app.models."""
    import app.models as _models
    _ = _models
    assert "calibraciones_usuario" not in Base.metadata.tables


def test_3_jobs_catalogo_sin_jobs_proyeccion():
    """(3) JOBS_CATALOGO no contiene 'proyeccion_negativa' ni 'recalcular_calibraciones_nocturno' y len == 24."""
    job_ids = [j.job_id for j in JOBS_CATALOGO]
    assert "proyeccion_negativa" not in job_ids
    assert "recalcular_calibraciones_nocturno" not in job_ids
    assert len(JOBS_CATALOGO) == 24


def test_4_main_y_scheduler_sin_funciones_jobs_eliminados():
    """(4) app.main no tiene job_proyeccion_negativa ni _job_recalcular_calibraciones, y notificacion_scheduler_service no tiene _job_proyeccion_negativa."""
    assert not hasattr(app_main, "job_proyeccion_negativa")
    assert not hasattr(app_main, "_job_recalcular_calibraciones")
    assert not hasattr(notificacion_scheduler_service, "_job_proyeccion_negativa")


@pytest.fixture(name="client_h3")
def client_h3_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models as _models
    _ = _models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSession()

    user = Usuario(
        id=uuid4(),
        email="test_h3_limpieza@argentum.test",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    def override_get_db():
        yield db

    def override_get_current_user():
        return user

    fastapi_app.dependency_overrides[get_db] = override_get_db
    fastapi_app.dependency_overrides[get_current_user] = override_get_current_user

    with TestClient(fastapi_app) as test_client:
        yield test_client

    fastapi_app.dependency_overrides.clear()
    db.close()


def test_5_endpoints_dashboard(client_h3: TestClient):
    """(5) Con TestClient: GET /dashboard/proyeccion -> 404 y GET /dashboard/fin-de-mes -> 200."""
    res_proy = client_h3.get("/dashboard/proyeccion")
    assert res_proy.status_code == 404

    res_fdm = client_h3.get("/dashboard/fin-de-mes")
    assert res_fdm.status_code == 200


def test_6_notificacion_proyeccion_negativa_se_mantiene():
    """(6) TipoNotificacion.PROYECCION_NEGATIVA sigue existiendo y resolver_canales_notificacion funciona."""
    assert hasattr(TipoNotificacion, "PROYECCION_NEGATIVA")

    config = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=uuid4(),
        proyeccion_negativa_activo=True,
        proyeccion_negativa_web=True,
        proyeccion_negativa_whatsapp=False,
    )
    canales = resolver_canales_notificacion(config, TipoNotificacion.PROYECCION_NEGATIVA)
    assert canales == (True, False)
