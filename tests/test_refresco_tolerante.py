"""
Tests para clasificar_tabla en refrescar_base_local.py.
Verifica la clasificación de tablas toleradas, iguales o con diferencia.
"""
import pytest
from scripts.local.refrescar_base_local import clasificar_tabla


@pytest.mark.parametrize(
    "tabla, cnt_prod, cnt_local, esperado",
    [
        ("transacciones", 1585, 1585, "OK"),
        ("ejecuciones_jobs", 5886, 5885, "TOLERADA"),
        ("user_refresh_tokens", 125, 120, "TOLERADA"),
        ("user_refresh_tokens", 126, 120, "DIFERENCIA"),
        ("ejecuciones_jobs", 5885, 5886, "DIFERENCIA"),
        ("transacciones", 1586, 1585, "DIFERENCIA"),
        ("ejecuciones_jobs", 10, -1, "DIFERENCIA"),
    ],
)
def test_clasificar_tabla(tabla: str, cnt_prod: int, cnt_local: int, esperado: str):
    """Verifica los casos exactos de clasificación requeridos."""
    assert clasificar_tabla(tabla, cnt_prod, cnt_local) == esperado
