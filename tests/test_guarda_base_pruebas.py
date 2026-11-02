"""
tests/test_guarda_base_pruebas.py — Pruebas de la guarda de seguridad para bases de datos en tests y arranque de la app.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.core.config import settings
from app.main import app
from tests.guarda_base import MENSAJE_BLOQUEO, base_permitida_para_pruebas


def test_base_permitida_para_pruebas():
    """Valida los casos de base_permitida_para_pruebas."""
    # URLs permitidas
    assert base_permitida_para_pruebas("postgresql://postgres:x@localhost:5433/argentum_local", {}) is True
    assert base_permitida_para_pruebas("postgresql://postgres:x@127.0.0.1:5433/argentum_local", {}) is True
    assert base_permitida_para_pruebas("postgresql://postgres:x@localhost:5432/argentum_ci", {"ARGENTUM_PG_TESTS_CI": "1"}) is True

    # URLs rechazadas
    assert base_permitida_para_pruebas("postgresql://postgres:x@localhost:5432/argentum_ci", {}) is False
    assert base_permitida_para_pruebas("postgresql://postgres:x@localhost:5432/argentum_local", {}) is False
    assert base_permitida_para_pruebas("postgresql://postgres:x@localhost:5433/otra", {}) is False
    assert base_permitida_para_pruebas("postgresql://u:p@db.ejemplo.com:5432/railway", {}) is False

    # Entornos de producción bloqueados
    assert base_permitida_para_pruebas("postgresql://postgres:x@localhost:5433/argentum_local", {"ARGENTUM_PRODUCCION_CONFIRMADA": "1"}) is False
    assert base_permitida_para_pruebas("postgresql://postgres:x@localhost:5433/argentum_local", {"RAILWAY_ENVIRONMENT_ID": "x"}) is False

    # SQLite y vacía
    assert base_permitida_para_pruebas("sqlite:///./x.db", {}) is True
    assert base_permitida_para_pruebas("", {}) is False


def test_subproceso_bloqueo_produccion():
    """
    Subproceso ejecutando pytest con URL remota y confirmación de producción debe
    terminar con returncode 2 y el MENSAJE_BLOQUEO sin conectar a la base.
    """
    repo_root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://u:p@db.ejemplo.com:5432/railway"
    env["ARGENTUM_PRODUCCION_CONFIRMADA"] = "1"

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_formato_montos.py",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    res = subprocess.run(
        cmd,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert res.returncode == 2
    salida_total = res.stdout + res.stderr
    assert MENSAJE_BLOQUEO in salida_total


def test_arranque_sin_inicializar_en_pruebas(monkeypatch):
    """
    Verifica que en pruebas con INICIALIZAR_BASE_AL_ARRANCAR=False no se llame
    a init_full_db ni a obtener_feriados_argentina, y que con True sí se llamen.
    """
    assert settings.INICIALIZAR_BASE_AL_ARRANCAR is False
    assert settings.ENABLE_SCHEDULER is False

    contador_init = 0

    def contar_init(*args, **kwargs):
        nonlocal contador_init
        contador_init += 1

    mock_feriados = AsyncMock(return_value=[])

    # 1. Caso por defecto: INICIALIZAR_BASE_AL_ARRANCAR=False -> 0 llamadas
    with patch("app.main.init_full_db", side_effect=contar_init), \
         patch("app.services.dias_habiles_service.obtener_feriados_argentina", mock_feriados):
        with TestClient(app):
            pass

    assert contador_init == 0
    assert mock_feriados.call_count == 0

    # 2. Caso con INICIALIZAR_BASE_AL_ARRANCAR=True -> 1 llamada a init_full_db y 3 a obtener_feriados_argentina
    monkeypatch.setattr(settings, "INICIALIZAR_BASE_AL_ARRANCAR", True)
    contador_init = 0
    mock_feriados.reset_mock()

    with patch("app.main.init_full_db", side_effect=contar_init), \
         patch("app.services.dias_habiles_service.obtener_feriados_argentina", mock_feriados):
        with TestClient(app):
            pass

    assert contador_init == 1
    assert mock_feriados.call_count == 3

