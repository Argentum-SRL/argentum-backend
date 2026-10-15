"""
Servicio para gestionar el impacto de movimientos en el saldo de billeteras.
Ubicación: app/services/saldo_billetera_service.py
"""
from __future__ import annotations

from datetime import date, timezone
from decimal import Decimal

from app.models.billetera import Billetera
from app.utils.fecha import TZ_ARGENTINA


def fecha_creacion_billetera(billetera: Billetera) -> date:
    """
    Retorna la fecha de creación de la billetera pasada a la hora de Argentina, como date.
    """
    fc = billetera.fecha_creacion
    if fc.tzinfo is None:
        fc = fc.replace(tzinfo=timezone.utc)
    return fc.astimezone(TZ_ARGENTINA).date()


def aplicar_impacto_saldo(billetera: Billetera, impacto: Decimal, fecha: date) -> None:
    """
    Aplica el impacto en el saldo de la billetera según su fecha respecto a la creación.
    impacto es lo que se suma a saldo_actual (+monto si es ingreso o entra, -monto si es egreso o sale).

    - Si fecha >= fecha de creación: billetera.saldo_actual += impacto (comportamiento normal).
    - Si fecha < fecha de creación: saldo_actual no cambia y billetera.saldo_inicial -= impacto.
      Un gasto anterior sube el saldo inicial y un ingreso anterior lo baja, así calcular_saldo_teorico
      sigue igual a saldo_actual.
    - Para deshacer se llama con -impacto y la fecha que tenía el movimiento.
    """
    if not isinstance(impacto, Decimal):
        impacto = Decimal(str(impacto))

    fc_date = fecha_creacion_billetera(billetera)

    if fecha >= fc_date:
        if billetera.saldo_actual is None:
            billetera.saldo_actual = Decimal("0.00")
        billetera.saldo_actual += impacto
    else:
        if billetera.saldo_inicial is None:
            billetera.saldo_inicial = Decimal("0.00")
        billetera.saldo_inicial -= impacto
