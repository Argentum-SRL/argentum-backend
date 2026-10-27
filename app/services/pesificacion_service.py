"""
Servicio para obtener la cotización de pesificación de consumos en dólares de un resumen de tarjeta.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services import dolar_service
from app.utils.fecha import hoy_argentina


def cotizacion_pesificacion(
    db: Session,
    fecha_pago: date | None,
    vencimiento: date,
) -> tuple[Decimal | None, date]:
    """
    Obtiene el dólar oficial vendedor aplicable para pesificar la parte en dólares de un resumen.
    Usa la fecha de pago (o hoy si no se indica) hasta el vencimiento como tope; si se paga
    después del vencimiento, toma la cotización del día del vencimiento.
    """
    fecha_base = fecha_pago or hoy_argentina()
    fecha_ref = fecha_base if fecha_base <= vencimiento else vencimiento
    cot_obj = dolar_service.obtener_cotizacion_por_fecha(db, "oficial", fecha_ref)
    if cot_obj is None or cot_obj.venta is None:
        return None, fecha_ref
    venta = Decimal(str(cot_obj.venta)).quantize(Decimal("0.01"))
    return venta, fecha_ref

