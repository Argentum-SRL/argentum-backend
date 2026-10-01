"""
tests/test_propagar_fechas_lote.py — Tests unitarios para la propagación de fechas en lotes de movimientos.
"""
import pytest
from app.routers.whatsapp.parsers import propagar_fechas_lote


def test_caso_1_principal_fecha_adicional_sin_fecha():
    """principal 27/09 + adicional sin fecha -> 27/09"""
    entidades = {
        "monto": 2900.0,
        "fecha": "2026-09-27",
        "transacciones_adicionales": [
            {"monto": 16900.0, "fecha": None},
        ],
    }
    propagar_fechas_lote(entidades)
    assert entidades["fecha"] == "2026-09-27"
    assert entidades["transacciones_adicionales"][0]["fecha"] == "2026-09-27"


def test_caso_2_secuencia_con_cambio_de_fecha():
    """principal 25/09, adicional sin fecha, adicional 27/09, adicional sin fecha -> 25, 25, 27, 27"""
    entidades = {
        "monto": 1000.0,
        "fecha": "2026-09-25",
        "transacciones_adicionales": [
            {"monto": 2000.0, "fecha": None},
            {"monto": 3000.0, "fecha": "2026-09-27"},
            {"monto": 4000.0, "fecha": None},
        ],
    }
    propagar_fechas_lote(entidades)
    assert entidades["fecha"] == "2026-09-25"
    assert entidades["transacciones_adicionales"][0]["fecha"] == "2026-09-25"
    assert entidades["transacciones_adicionales"][1]["fecha"] == "2026-09-27"
    assert entidades["transacciones_adicionales"][2]["fecha"] == "2026-09-27"


def test_caso_3_todo_sin_fecha():
    """todo sin fecha -> todo sin fecha"""
    entidades = {
        "monto": 1000.0,
        "fecha": None,
        "transacciones_adicionales": [
            {"monto": 2000.0, "fecha": None},
            {"monto": 3000.0, "fecha": None},
        ],
    }
    propagar_fechas_lote(entidades)
    assert entidades["fecha"] is None
    assert entidades["transacciones_adicionales"][0]["fecha"] is None
    assert entidades["transacciones_adicionales"][1]["fecha"] is None


def test_caso_4_principal_sin_fecha_adicional_con_fecha():
    """principal sin fecha + adicional 27/09 -> el principal queda sin fecha"""
    entidades = {
        "monto": 1000.0,
        "fecha": None,
        "transacciones_adicionales": [
            {"monto": 2000.0, "fecha": "2026-09-27"},
        ],
    }
    propagar_fechas_lote(entidades)
    assert entidades["fecha"] is None
    assert entidades["transacciones_adicionales"][0]["fecha"] == "2026-09-27"
