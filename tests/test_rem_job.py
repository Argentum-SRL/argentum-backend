# -*- coding: utf-8 -*-
"""
tests/test_rem_job.py — Pruebas unitarias para la corrección del job de actualización del REM.
Cubre T-1 (bajar_rem), T-2 (firmas AST de jobs en app/main.py) y T-3 (_job_actualizar_rem).
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import uuid
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.core.registro_jobs import registrar_ejecucion
from app.main import _job_actualizar_rem
from app.models.expectativa_inflacion import ExpectativaInflacion
from app.services.inflacion_esperada_service import bajar_rem
from app.utils.fecha import hoy_argentina


# Setup SQLite compatibility para pruebas en memoria
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


# ---------------------------------------------------------------------------
# T-1. Comportamiento y retorno de bajar_rem
# ---------------------------------------------------------------------------

def test_t1_a_bajar_rem_descarga_exitosa(db: Session):
    fixture_path = Path(__file__).parent / "fixtures" / "rem_2026_09.xlsx"
    contenido = fixture_path.read_bytes()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = contenido

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        res = bajar_rem(db, hoy=date(2026, 10, 15))

    assert res == "guardado 2026-09"
    filas = db.query(ExpectativaInflacion).filter(
        ExpectativaInflacion.relevamiento == "2026-09"
    ).all()
    assert len(filas) == 8


def test_t1_b_bajar_rem_ya_estaba(db: Session):
    ahora = datetime.now(timezone.utc)
    db.add(ExpectativaInflacion(
        id=uuid4(),
        relevamiento="2026-10",
        periodo="2026-11",
        valor=Decimal("2.00"),
        fuente="REM BCRA",
        fecha_actualizacion=ahora,
    ))
    db.commit()

    with patch("httpx.Client") as mock_client_cls:
        res = bajar_rem(db, hoy=date(2026, 11, 12))
        mock_client_cls.assert_not_called()

    assert res == "ya estaba 2026-10"


def test_t1_c_bajar_rem_ambas_404(db: Session):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.content = b""

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("app.services.inflacion_esperada_service.enviar_alerta_admin"):
            res = bajar_rem(db, hoy=date(2026, 11, 5))

    assert res == "no se pudo bajar: 2026-10 → 404; 2026-09 → 404"


def test_t1_d_bajar_rem_connect_error(db: Session):
    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.side_effect = httpx.ConnectError("x")
        mock_client_cls.return_value = mock_client

        with patch("app.services.inflacion_esperada_service.enviar_alerta_admin"):
            res = bajar_rem(db, hoy=date(2026, 11, 5))

    assert res == "no se pudo bajar: 2026-10 → ConnectError; 2026-09 → ConnectError"


def test_t1_e_bajar_rem_404_y_archivo_vacio(db: Session):
    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.content = b""

    resp_vacio = MagicMock()
    resp_vacio.status_code = 200
    resp_vacio.content = b"x"

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.side_effect = [resp_404, resp_vacio]
        mock_client_cls.return_value = mock_client

        with patch("app.services.inflacion_esperada_service.enviar_alerta_admin"):
            res = bajar_rem(db, hoy=date(2026, 11, 5))

    assert res == "no se pudo bajar: 2026-10 → 404; 2026-09 → archivo vacío"


def test_t1_f_bajar_rem_error_inesperado(db: Session):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"x" * 2000

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("app.services.inflacion_esperada_service.leer_rem_xlsx", side_effect=ValueError("roto")):
            res = bajar_rem(db, hoy=date(2026, 11, 5))

    assert isinstance(res, str)
    assert res.startswith("error: ")
    assert "ValueError" in res


# ---------------------------------------------------------------------------
# T-2. Firmas AST de los jobs en app/main.py
# ---------------------------------------------------------------------------

def _verificar_firmas_jobs_ast(main_path: Path) -> list[str]:
    backend_dir = main_path.parent.parent
    code = main_path.read_text(encoding="utf-8")
    tree = ast.parse(code, filename=str(main_path))

    faltantes: list[str] = []

    for item in tree.body:
        if not (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and "job" in item.name):
            continue

        job_fn_name = item.name

        internal_imports: dict[str, tuple[str, str]] = {}
        for stmt in ast.walk(item):
            if isinstance(stmt, ast.ImportFrom) and stmt.module and stmt.module.startswith("app"):
                for alias in stmt.names:
                    local_name = alias.asname or alias.name
                    internal_imports[local_name] = (stmt.module, alias.name)

        if not internal_imports:
            continue

        for node in ast.walk(item):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called_name = node.func.id
                if called_name not in internal_imports:
                    continue

                mod_name, orig_func_name = internal_imports[called_name]
                rel_parts = mod_name.split(".")
                mod_file = backend_dir / (os.path.join(*rel_parts) + ".py")
                if not mod_file.exists():
                    pkg_file = backend_dir / os.path.join(*rel_parts) / "__init__.py"
                    if pkg_file.exists():
                        mod_file = pkg_file
                    else:
                        continue

                mod_code = mod_file.read_text(encoding="utf-8")
                mod_tree = ast.parse(mod_code, filename=str(mod_file))

                target_def = None
                for top in mod_tree.body:
                    if isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)) and top.name == orig_func_name:
                        target_def = top
                        break

                if target_def is None:
                    continue

                fn_args = target_def.args

                num_defaults = len(fn_args.defaults)
                if num_defaults > 0:
                    mand_pos = [a.arg for a in fn_args.args[:-num_defaults]]
                else:
                    mand_pos = [a.arg for a in fn_args.args]

                mand_kw = []
                for kw_arg, kw_def in zip(fn_args.kwonlyargs, fn_args.kw_defaults):
                    if kw_def is None:
                        mand_kw.append(kw_arg.arg)

                todos_obligatorios = mand_pos + mand_kw

                cubiertos_pos = [a.arg for a in fn_args.args[:len(node.args)]]
                cubiertos_kw = [k.arg for k in node.keywords if k.arg is not None]
                cubiertos = set(cubiertos_pos) | set(cubiertos_kw)

                faltan = [arg for arg in todos_obligatorios if arg not in cubiertos]
                if faltan:
                    faltantes.append(f"{job_fn_name} → {called_name}: faltan {faltan}")

    return faltantes


def test_t2_firmas_jobs_app_main():
    main_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
    faltantes = _verificar_firmas_jobs_ast(main_path)
    assert faltantes == [], f"Se encontraron llamadas sin argumentos obligatorios: {faltantes}"


# ---------------------------------------------------------------------------
# T-3. Comportamiento de _job_actualizar_rem
# ---------------------------------------------------------------------------

def test_t3_a_argumentos_bajar_rem():
    mock_db = MagicMock()
    with patch("app.services.inflacion_esperada_service.bajar_rem", return_value="guardado 2026-09") as mock_bajar:
        with patch("app.main.SessionLocal", return_value=mock_db):
            with patch("app.main.intentar_tomar_lock_job", return_value=True):
                with patch("app.main.liberar_lock_job"):
                    _job_actualizar_rem()
                    mock_bajar.assert_called_once()
                    call_args, call_kwargs = mock_bajar.call_args
                    assert len(call_args) == 2, f"Se esperaban 2 argumentos posicionales, llegaron {len(call_args)}"
                    assert len(call_kwargs) == 0, f"No se esperaban kwargs, llegaron {call_kwargs}"
                    assert call_args[0] is mock_db
                    assert isinstance(call_args[1], date)
                    assert call_args[1] == hoy_argentina()


def test_t3_b_retornos_exitosos_no_lanzan():
    mock_db = MagicMock()
    for retorno in ["guardado 2026-09", "ya estaba 2026-10"]:
        with patch("app.services.inflacion_esperada_service.bajar_rem", return_value=retorno):
            with patch("app.main.SessionLocal", return_value=mock_db):
                with patch("app.main.intentar_tomar_lock_job", return_value=True):
                    with patch("app.main.liberar_lock_job"):
                        _job_actualizar_rem()


def test_t3_c_falla_lanza_runtime_error_con_recursos_liberados():
    eventos = []
    mock_db = MagicMock()
    mock_db.close.side_effect = lambda: eventos.append("close")
    mock_liberar = MagicMock()
    mock_liberar.side_effect = lambda db, name: eventos.append("liberar")

    texto_falla = "no se pudo bajar: 2026-10 → 404; 2026-09 → ConnectError"
    with patch("app.services.inflacion_esperada_service.bajar_rem", return_value=texto_falla):
        with patch("app.main.SessionLocal", return_value=mock_db):
            with patch("app.main.intentar_tomar_lock_job", return_value=True):
                with patch("app.main.liberar_lock_job", side_effect=mock_liberar.side_effect):
                    try:
                        _job_actualizar_rem()
                        pytest.fail("Debió lanzar RuntimeError")
                    except RuntimeError as exc:
                        eventos.append("raise")
                        assert str(exc) == texto_falla

    assert "liberar" in eventos
    assert "close" in eventos
    assert "raise" in eventos
    assert eventos.index("liberar") < eventos.index("raise")
    assert eventos.index("close") < eventos.index("raise")


def test_t3_d_lock_omitido():
    mock_db = MagicMock()
    with patch("app.services.inflacion_esperada_service.bajar_rem") as mock_bajar:
        with patch("app.main.SessionLocal", return_value=mock_db):
            with patch("app.main.intentar_tomar_lock_job", return_value=False):
                with patch("app.main.liberar_lock_job") as mock_liberar:
                    _job_actualizar_rem()
                    mock_bajar.assert_not_called()
                    mock_liberar.assert_not_called()


def test_t3_e_registrar_ejecucion_error_anotado():
    mock_db = MagicMock()
    texto_falla = "no se pudo bajar: 2026-10 → 404; 2026-09 → 404"
    with patch("app.services.inflacion_esperada_service.bajar_rem", return_value=texto_falla):
        with patch("app.main.SessionLocal", return_value=mock_db):
            with patch("app.main.intentar_tomar_lock_job", return_value=True):
                with patch("app.main.liberar_lock_job"):
                    with patch("app.core.registro_jobs.guardar_registro_ejecucion") as mock_guardar:
                        wrapped = registrar_ejecucion("actualizar_rem", _job_actualizar_rem)
                        with pytest.raises(RuntimeError):
                            wrapped()
                        mock_guardar.assert_called_once()
                        _, kwargs = mock_guardar.call_args
                        assert kwargs.get("job_id") == "actualizar_rem"
                        assert kwargs.get("resultado") == "error"
                        assert kwargs.get("detalle") == f"RuntimeError: {texto_falla}"

