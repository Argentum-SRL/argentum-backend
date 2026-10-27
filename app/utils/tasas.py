"""
Conversión de tasas para herramientas financieras.
"""
from __future__ import annotations


def tna_a_mensual(tna_porcentaje: float | int) -> float:
    """
    Convierte una TNA en porcentaje a fracción mensual (tna / 100 / 12).
    """
    return (float(tna_porcentaje) / 100.0) / 12.0

