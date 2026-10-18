"""
app/routers/whatsapp/aviso_saldo_wpp.py — Aviso de movimientos con fecha anterior al saldo de billeteras.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.transaccion import MetodoPago, Transaccion
from app.services.saldo_billetera_service import (
    fecha_creacion_billetera,
    movimiento_anterior_al_saldo,
)


def formatear_fecha_ddmmaaaa(f: date | datetime) -> str:
    """Formatea una fecha como dd/mm/aaaa."""
    f_date = f.date() if isinstance(f, datetime) else f
    return f_date.strftime("%d/%m/%Y")


def texto_aviso_saldo_individual(billetera: Billetera, fecha_efectiva: date | None = None) -> str:
    """Genera el texto exacto de aviso para un movimiento individual."""
    if fecha_efectiva is None:
        fecha_efectiva = fecha_creacion_billetera(billetera)
    f_str = formatear_fecha_ddmmaaaa(fecha_efectiva)
    return (
        f"Ojo: es anterior al saldo de tu billetera «{billetera.nombre}» ({f_str}), "
        "así que cuenta en tus gastos del mes pero no cambia lo que tenés hoy."
    )


def texto_aviso_saldo_lote(billetera: Billetera, cantidad: int, fecha_efectiva: date | None = None) -> str:
    """Genera el texto exacto de aviso en lote para una billetera dada."""
    if fecha_efectiva is None:
        fecha_efectiva = fecha_creacion_billetera(billetera)
    f_str = formatear_fecha_ddmmaaaa(fecha_efectiva)
    return (
        f"Ojo: {cantidad} de estos movimientos son anteriores al saldo de tu billetera «{billetera.nombre}» ({f_str}): "
        "cuentan en tus gastos del mes pero no cambian lo que tenés hoy."
    )


def obtener_aviso_saldo_individual(
    transaccion: Transaccion,
    billetera: Billetera | None = None,
    db: Session | None = None,
) -> str | None:
    """
    Evalúa si la transacción individual es anterior al saldo de su billetera y genera el texto.
    Si no se tiene la billetera en mano, se busca por transaccion.billetera_id.
    """
    if transaccion.metodo_pago == MetodoPago.CREDITO or str(transaccion.metodo_pago).lower() == "credito":
        return None

    if billetera is None and transaccion.billetera_id and db is not None:
        billetera = db.get(Billetera, transaccion.billetera_id)

    if billetera is None:
        return None

    if movimiento_anterior_al_saldo(billetera, transaccion.fecha, transaccion.metodo_pago):
        return texto_aviso_saldo_individual(billetera)

    return None


def obtener_aviso_saldo_lote(
    transacciones: Sequence[Transaccion],
    db: Session | None = None,
    billeteras_map: dict[UUID, Billetera] | None = None,
) -> str | None:
    """
    Evalúa las transacciones del lote y genera una sola línea por billetera con movimientos anteriores.
    """
    conteo_por_billetera: dict[UUID, int] = {}
    billeteras_obj: dict[UUID, Billetera] = dict(billeteras_map or {})

    for tx in transacciones:
        if tx.metodo_pago == MetodoPago.CREDITO or str(tx.metodo_pago).lower() == "credito":
            continue
        if not tx.billetera_id:
            continue

        b_id = tx.billetera_id
        if b_id not in billeteras_obj and db is not None:
            b_found = db.get(Billetera, b_id)
            if b_found:
                billeteras_obj[b_id] = b_found

        b = billeteras_obj.get(b_id)
        if not b:
            continue

        if movimiento_anterior_al_saldo(b, tx.fecha, tx.metodo_pago):
            conteo_por_billetera[b_id] = conteo_por_billetera.get(b_id, 0) + 1

    if not conteo_por_billetera:
        return None

    lineas = []
    for b_id, cant in conteo_por_billetera.items():
        b = billeteras_obj[b_id]
        lineas.append(texto_aviso_saldo_lote(b, cant))

    return "\n".join(lineas)
