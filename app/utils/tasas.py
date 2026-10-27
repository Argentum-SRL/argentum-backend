"""
Conversión de tasas para herramientas financieras.
"""
from __future__ import annotations

from decimal import Decimal


def tna_a_mensual(tna_porcentaje: Decimal | float | int) -> Decimal:
    """
    Convierte una TNA en porcentaje a fracción mensual (tna / 100 / 12) como Decimal.
    """
    return Decimal(str(tna_porcentaje)) / Decimal("100") / Decimal("12")
