# -*- coding: utf-8 -*-
"""
tests/test_analisis_n_informes.py
Pruebas de aislamiento multiusuario para informes personalizados (TN14) y de migración Alembic (TN15).
"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, MetaData, Table, create_engine, inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.analisis_informes_service import (
    actualizar_informe_usuario,
    crear_informe_usuario,
    eliminar_informe_usuario,
    obtener_informe_usuario,
)
from app.utils.fecha import TZ_ARGENTINA


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
                    import uuid
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    import uuid
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


def _crear_usuario(db: Session, email: str) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2025, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(u)
    db.commit()
    return u


# ── TN14: Aislamiento estricto de informes entre usuarios ───────────────────
def test_tn14_informes_otro_usuario(db: Session):
    usuario_a = _crear_usuario(db, "usuario_a@argentum.com")
    usuario_b = _crear_usuario(db, "usuario_b@argentum.com")

    # Usuario A crea su informe
    informe_a = crear_informe_usuario(
        db,
        usuario_a,
        nombre="Informe Confidencial A",
        descripcion="Datos financieros privados de A",
        icono="pie-chart",
        es_favorito=True,
        configuracion={
            "moneda_modo": "solo_ars",
            "ciclos": 6,
            "naturaleza": "todos",
            "bloques": [{"id": "b1", "tipo": "kpis", "titulo": "Números principales"}],
        },
    )
    assert informe_a["id"] is not None
    assert informe_a["nombre"] == "Informe Confidencial A"
    inf_id = UUID(informe_a["id"])

    # 1. Usuario B intenta obtener el informe de A -> Retorna None (aislamiento)
    informe_visto_por_b = obtener_informe_usuario(db, usuario_b, inf_id)
    assert informe_visto_por_b is None

    # 2. Usuario B intenta editar el informe de A -> Retorna None y no modifica
    resultado_update = actualizar_informe_usuario(
        db,
        usuario_b,
        inf_id,
        {"nombre": "Hackeado por B", "descripcion": "Intento de sobrescritura"},
    )
    assert resultado_update is None

    # Verificar que el nombre del informe de A no cambió
    informe_a_verif = obtener_informe_usuario(db, usuario_a, inf_id)
    assert informe_a_verif is not None
    assert informe_a_verif["nombre"] == "Informe Confidencial A"

    # 3. Usuario B intenta borrar el informe de A -> Retorna False y no borra
    resultado_delete = eliminar_informe_usuario(db, usuario_b, inf_id)
    assert resultado_delete is False

    # El informe de A sigue existiendo intacto
    informe_a_post_delete = obtener_informe_usuario(db, usuario_a, inf_id)
    assert informe_a_post_delete is not None
    assert UUID(informe_a_post_delete["id"]) == inf_id


# ── TN15: Migración idempotente de informes_personalizados ───────────────────
def test_tn15_migracion_informes():
    backend_root = Path(__file__).resolve().parent.parent
    migr_path = backend_root / "alembic" / "versions" / "b6c7d8e9f0a1_informes_personalizados.py"
    assert migr_path.is_file(), f"No se encontró el archivo de migración en {migr_path}"

    spec = importlib.util.spec_from_file_location("b6c7d8e9f0a1_informes_personalizados", migr_path)
    migr_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migr_mod)

    # Base en memoria aislada para probar la migración
    engine = create_engine("sqlite:///:memory:")

    # Crear tabla usuarios previa requerida por la foreign key
    with engine.begin() as conn:
        meta = MetaData()
        Table("usuarios", meta, Column("id", types.CHAR(36), primary_key=True))
        meta.create_all(conn)

    # Paso 1: Base sin la tabla -> upgrade() la crea
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migr_mod.upgrade()

    insp = inspect(engine)
    assert insp.has_table("informes_personalizados") is True

    # Paso 2: Base con la tabla ya existente -> upgrade() no falla (idempotencia)
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migr_mod.upgrade()

    insp2 = inspect(engine)
    assert insp2.has_table("informes_personalizados") is True

    # Paso 3: downgrade() elimina la tabla limpiamente
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migr_mod.downgrade()

    insp3 = inspect(engine)
    assert insp3.has_table("informes_personalizados") is False
