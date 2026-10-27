"""
Funciones únicas para obtener el monto efectivo de una cuota.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func

from app.models.cuota import Cuota


def monto_efectivo_cuota(cuota: Cuota | Any) -> Decimal:
    """
    Devuelve el monto efectivo de una cuota: monto_real si no es None,
    si no monto_proyectado, y si los dos son None Decimal("0.00").
    """
    monto = cuota.monto_real if cuota.monto_real is not None else cuota.monto_proyectado
    if monto is None:
        return Decimal("0.00")
    return monto if isinstance(monto, Decimal) else Decimal(str(monto))


def monto_efectivo_cuota_sql():
    """
    Expresión SQL para obtener el monto efectivo de una cuota.
    """
    return func.coalesce(Cuota.monto_real, Cuota.monto_proyectado, Decimal("0.00"))
