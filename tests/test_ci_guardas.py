"""
tests/test_ci_guardas.py — Pruebas de guardas de seguridad para scripts de CI y conftest.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import uuid
import pytest

ROOT = Path(__file__).resolve().parent.parent
PATH_CARGAR_ESQUEMA = ROOT / "scripts" / "ci" / "cargar_esquema.py"
PATH_CONFTEST_PG = ROOT / "tests" / "pg" / "conftest.py"


def _cargar_modulo(prefijo: str, ruta: Path):
    nombre = f"{prefijo}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_url_permitida():
    """Valida los filtros de url_permitida para cargar_esquema.py."""
    mod = _cargar_modulo("mod_cargar_esquema", PATH_CARGAR_ESQUEMA)
    assert mod.url_permitida("postgresql://postgres:x@localhost:5432/argentum_ci") is True
    assert mod.url_permitida("postgresql://postgres:x@127.0.0.1:5433/argentum_ci_prueba") is True
    assert mod.url_permitida("postgresql://postgres:x@localhost:5433/argentum_local") is False
    assert mod.url_permitida("postgresql://u:p@db.ejemplo.com:5432/argentum_ci") is False
    assert mod.url_permitida("sqlite:///./dummy.db") is False


def test_cargar_esquema_main_rechaza_base_no_permitida(monkeypatch):
    """
    main() con DATABASE_URL apuntando a base local distinta de CI debe salir con código 2
    sin llamar a psycopg2.connect.
    """
    mod = _cargar_modulo("mod_cargar_esquema", PATH_CARGAR_ESQUEMA)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:x@localhost:5433/argentum_local")

    def _fallar_si_se_llama(*args, **kwargs):
        pytest.fail("psycopg2.connect no debe ser llamado")

    monkeypatch.setattr(mod.psycopg2, "connect", _fallar_si_se_llama)

    with pytest.raises(SystemExit) as exc_info:
        mod.main()
    assert exc_info.value.code == 2


def test_conftest_guarda_bloquea_url_remota(monkeypatch):
    """Guarda de tests/pg/conftest.py bloquea URL remota con RuntimeError."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.ejemplo.com:5432/otra")
    monkeypatch.delenv("ARGENTUM_PG_TESTS_CI", raising=False)
    with pytest.raises(RuntimeError, match=r"^tests/pg solo corre contra la copia local o la base de la CI\.$"):
        _cargar_modulo("mod_conftest_pg", PATH_CONFTEST_PG)


def test_conftest_guarda_bloquea_ci_sin_variable(monkeypatch):
    """Guarda de tests/pg/conftest.py bloquea base argentum_ci si falta ARGENTUM_PG_TESTS_CI."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:x@localhost:5432/argentum_ci")
    monkeypatch.delenv("ARGENTUM_PG_TESTS_CI", raising=False)
    with pytest.raises(RuntimeError, match=r"^tests/pg solo corre contra la copia local o la base de la CI\.$"):
        _cargar_modulo("mod_conftest_pg", PATH_CONFTEST_PG)


def test_conftest_guarda_permite_sqlite(monkeypatch):
    """Guarda de tests/pg/conftest.py permite SQLite sin error."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./dummy.db")
    monkeypatch.delenv("ARGENTUM_PG_TESTS_CI", raising=False)
    mod = _cargar_modulo("mod_conftest_pg", PATH_CONFTEST_PG)
    assert mod is not None


def test_conftest_guarda_permite_local(monkeypatch):
    """Guarda de tests/pg/conftest.py permite la base local argentum_local sin error."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:x@localhost:5433/argentum_local")
    monkeypatch.delenv("ARGENTUM_PG_TESTS_CI", raising=False)
    mod = _cargar_modulo("mod_conftest_pg", PATH_CONFTEST_PG)
    assert mod is not None


def test_conftest_guarda_permite_ci_con_variable(monkeypatch):
    """Guarda de tests/pg/conftest.py permite la base argentum_ci con ARGENTUM_PG_TESTS_CI=1."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:x@localhost:5432/argentum_ci")
    monkeypatch.setenv("ARGENTUM_PG_TESTS_CI", "1")
    mod = _cargar_modulo("mod_conftest_pg", PATH_CONFTEST_PG)
    assert mod is not None
