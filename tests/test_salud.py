"""
tests/test_salud.py — Pruebas para la ruta de salud GET /salud.
"""
from __future__ import annotations

from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.main import app
from app.core.database import get_db


def test_salud_ok():
    """Prueba 1: GET /salud responde 200 con estado ok cuando la base funciona."""
    client = TestClient(app)
    response = client.get("/salud")
    assert response.status_code == 200
    assert response.json() == {"estado": "ok"}


def test_salud_db_error():
    """Prueba 2: Con la sesion de base parcheada para que execute lance excepcion -> 503 estado error."""
    mock_db = MagicMock()
    mock_db.execute.side_effect = OperationalError("SELECT 1", {}, Exception("Conexion perdida simulada"))

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    try:
        client = TestClient(app)
        response = client.get("/salud")
        assert response.status_code == 503
        assert response.json() == {"estado": "error"}
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_salud_origin_cualquiera():
    """Prueba 3: GET /salud con un Origin cualquiera responde 200 (es GET)."""
    client = TestClient(app)
    response = client.get("/salud", headers={"Origin": "https://sitio-desconocido.ejemplo.com"})
    assert response.status_code == 200
    assert response.json() == {"estado": "ok"}
