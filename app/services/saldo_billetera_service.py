"""
Servicio para gestionar el impacto de movimientos en el saldo de billeteras.
Ubicación: app/services/saldo_billetera_service.py

Regla de historia y punto de partida:
El saldo inicial representa una foto con fecha (punto de partida) del estado inicial de una billetera.
El saldo inicial NUNCA es un ingreso, sueldo ni ahorro, y no entra en ingreso habitual,
gasto típico, tasa de ahorro, patrones, presupuestos ni balance del ciclo.
Hoy se cumple de manera estructural porque el saldo inicial no es un movimiento registrado (no es Transacción ni Transferencia).
Para billeteras sin saldo inicial (o creadas con saldo 0), no hay punto de partida (tiene_punto_partida = False),
por lo que todo movimiento cuenta y afecta directamente el saldo actual, sin importar su fecha.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.billetera import Billetera
from app.models.transaccion import MetodoPago
from app.utils.fecha import TZ_ARGENTINA


def fecha_creacion_billetera(billetera: Billetera) -> date:
    """
    Retorna la fecha efectiva del saldo de la billetera:
    fecha_saldo si existe; si no, la fecha de creación en hora Argentina como date.
    """
    if getattr(billetera, "fecha_saldo", None) is not None:
        return billetera.fecha_saldo

    fc = billetera.fecha_creacion
    if fc.tzinfo is None:
        fc = fc.replace(tzinfo=timezone.utc)
    return fc.astimezone(TZ_ARGENTINA).date()


def fecha_punto_partida(billetera: Billetera) -> date | None:
    """
    Retorna la fecha del punto de partida de la billetera:
    - None si no tiene punto de partida (billetera.tiene_punto_partida es False).
    - Si tiene punto de partida: igual que fecha_creacion_billetera(billetera).
    """
    if not getattr(billetera, "tiene_punto_partida", True):
        return None
    return fecha_creacion_billetera(billetera)


def movimiento_anterior_al_saldo(
    billetera: Billetera | None,
    fecha: date | datetime,
    metodo_pago: MetodoPago | str | None = None,
) -> bool:
    """
    Indica si un movimiento es anterior a la fecha efectiva de saldo de la billetera
    y no corresponde a un método de pago de crédito.
    Si la billetera no tiene punto de partida, devuelve False.
    """
    if billetera is None:
        return False

    if metodo_pago is not None:
        val = metodo_pago.value if hasattr(metodo_pago, "value") else str(metodo_pago)
        if val.lower() == "credito":
            return False

    f_pp = fecha_punto_partida(billetera)
    if f_pp is None:
        return False

    f_date = fecha.date() if isinstance(fecha, datetime) else fecha
    return f_date < f_pp


def aplicar_impacto_saldo(billetera: Billetera, impacto: Decimal, fecha: date) -> None:
    """
    Aplica el impacto en el saldo de la billetera según su fecha respecto al punto de partida.
    impacto es lo que se suma a saldo_actual (+monto si es ingreso o entra, -monto si es egreso o sale).

    - Si fecha_punto_partida es None o fecha >= fecha_punto_partida:
      se aplica a saldo_actual (comportamiento normal / billetera sin punto de partida).
    - Si fecha < fecha_punto_partida (y tiene punto de partida):
      saldo_actual no cambia y billetera.saldo_inicial -= impacto.
    - Para deshacer se llama con -impacto y la fecha que tenía el movimiento.
    """
    if not isinstance(impacto, Decimal):
        impacto = Decimal(str(impacto))

    f_pp = fecha_punto_partida(billetera)

    if f_pp is None or fecha >= f_pp:
        if billetera.saldo_actual is None:
            billetera.saldo_actual = Decimal("0.00")
        billetera.saldo_actual += impacto
    else:
        if billetera.saldo_inicial is None:
            billetera.saldo_inicial = Decimal("0.00")
        billetera.saldo_inicial -= impacto
