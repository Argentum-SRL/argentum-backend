"""
Funciones únicas para obtener el monto efectivo y plan de cálculo de cuotas.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
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


def calcular_plan_cuotas(
    monto_total: Decimal,
    cantidad_cuotas: int,
    tasa_mensual: Decimal | None
) -> dict[str, Any]:
    """
    Calcula de forma unificada el plan de cuotas (monto de cuota, total financiado y última cuota).
    - Cuota exacta: fórmula de amortización francesa si tasa_mensual > 0, si no monto_total / cantidad_cuotas.
    - monto_cuota: cuota exacta redondeada ROUND_HALF_UP a centavos.
    - total_financiado: (cuota exacta * cantidad_cuotas) redondeado ROUND_HALF_UP a centavos.
    - ultima_cuota: monto_cuota + (total_financiado - monto_cuota * cantidad_cuotas).
    """
    monto_d = monto_total if isinstance(monto_total, Decimal) else Decimal(str(monto_total))
    cant = int(cantidad_cuotas)
    cant_dec = Decimal(cant)

    if tasa_mensual is not None:
        tm = tasa_mensual if isinstance(tasa_mensual, Decimal) else Decimal(str(tasa_mensual))
    else:
        tm = None

    if tm is not None and tm > Decimal("0"):
        factor = (Decimal("1") + tm) ** cant
        cuota_exacta = monto_d * (tm * factor) / (factor - Decimal("1"))
    else:
        cuota_exacta = monto_d / cant_dec

    centavo = Decimal("0.01")
    monto_cuota = cuota_exacta.quantize(centavo, rounding=ROUND_HALF_UP)
    total_financiado = (cuota_exacta * cant_dec).quantize(centavo, rounding=ROUND_HALF_UP)
    ultima_cuota = monto_cuota + (total_financiado - (monto_cuota * cant_dec))

    return {
        "cantidad_cuotas": cant,
        "monto_cuota": monto_cuota,
        "total_financiado": total_financiado,
        "ultima_cuota": ultima_cuota,
    }
