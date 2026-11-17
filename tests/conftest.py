"""
tests/conftest.py — Configuración global de pytest para Argentum.
"""
from __future__ import annotations

import os

os.environ.setdefault("ENABLE_SCHEDULER", "false")
# En las pruebas la app nunca inicializa la base real ni precarga feriados al arrancar.
os.environ["INICIALIZAR_BASE_AL_ARRANCAR"] = "false"

import pytest
from tests.guarda_base import MENSAJE_BLOQUEO, base_permitida_para_pruebas

pytest_plugins = ["tests.plugin_reglas_texto"]


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "sin_reglas_texto: desactiva el control de textos WPP para este test",
    )
    url = os.environ.get("DATABASE_URL")
    if not url:
        from app.core.config import settings
        url = str(settings.DATABASE_URL)
    if not base_permitida_para_pruebas(url, os.environ):
        pytest.exit(MENSAJE_BLOQUEO, returncode=2)

