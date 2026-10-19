"""
app/routers/whatsapp/aviso_saldo_wpp.py — Aviso de movimientos con fecha anterior al saldo de billeteras.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.transaccion import MetodoPago, Transaccion
from app.services.saldo_billetera_service import (
    fecha_creacion_billetera,
    fecha_punto_partida,
    movimiento_anterior_al_saldo,
)


def recortar_nombre_billetera(nombre: str) -> str:
    """Recorta el nombre de la billetera a 25 caracteres (primeros 24 más '…' si es más largo)."""
    if len(nombre) > 25:
        return nombre[:24] + "…"
    return nombre


def formatear_fecha_ddmm(f: date | datetime) -> str:
    """Formatea una fecha como dd/mm."""
    f_date = f.date() if isinstance(f, datetime) else f
    return f_date.strftime("%d/%m")


def formatear_fecha_ddmmaaaa(f: date | datetime) -> str:
    """Formatea una fecha como dd/mm/aaaa (mantiene compatibilidad)."""
    f_date = f.date() if isinstance(f, datetime) else f
    return f_date.strftime("%d/%m/%Y")


def texto_aviso_saldo_individual(billetera: Billetera, fecha_efectiva: date | None = None) -> str:
    """Genera el texto exacto de aviso para 1 movimiento individual."""
    if fecha_efectiva is None:
        fecha_efectiva = fecha_punto_partida(billetera) or fecha_creacion_billetera(billetera)
    f_str = formatear_fecha_ddmm(fecha_efectiva)
    nom = recortar_nombre_billetera(billetera.nombre)
    return f"Ojo: es anterior a tu saldo de «{nom}» ({f_str}) y no lo modifica."


def texto_aviso_saldo_lote(billetera: Billetera, cantidad: int, fecha_efectiva: date | None = None) -> str:
    """Genera el texto exacto de aviso en lote para una billetera dada."""
    if cantidad == 1:
        return texto_aviso_saldo_individual(billetera, fecha_efectiva=fecha_efectiva)
    if fecha_efectiva is None:
        fecha_efectiva = fecha_punto_partida(billetera) or fecha_creacion_billetera(billetera)
    f_str = formatear_fecha_ddmm(fecha_efectiva)
    nom = recortar_nombre_billetera(billetera.nombre)
    return f"Ojo: {cantidad} son anteriores a tu saldo de «{nom}» ({f_str}) y no lo modifican."


def obtener_aviso_saldo(
    movimientos: Sequence[Any],
    db: Session | None = None,
    billeteras_map: dict[UUID, Billetera] | None = None,
    billetera_defecto: Billetera | None = None,
) -> str | None:
    """
    Función única que recibe los movimientos del mensaje (billetera, fecha, metodo_pago),
    cuenta solo los que cumplen movimiento_anterior_al_saldo y devuelve el texto de una línea (o None si son 0).
    """
    if not movimientos:
        return None

    billeteras_obj: dict[UUID, Billetera] = dict(billeteras_map or {})
    if billetera_defecto and billetera_defecto.id:
        billeteras_obj[billetera_defecto.id] = billetera_defecto

    # Conteo de movimientos válidos por billetera
    conteo_por_billetera: dict[UUID, int] = {}

    for mov in movimientos:
        # Extraer fecha, metodo_pago y billetera
        if isinstance(mov, dict):
            fecha = mov.get("fecha")
            metodo_pago = mov.get("metodo_pago")
            b_item = mov.get("billetera")
            b_id = mov.get("billetera_id") or (b_item.id if b_item else None)
        else:
            fecha = getattr(mov, "fecha", None)
            metodo_pago = getattr(mov, "metodo_pago", None)
            b_item = getattr(mov, "billetera", None)
            b_id = getattr(mov, "billetera_id", None) or (b_item.id if b_item else None)

        if not fecha:
            continue

        # Excluir crédito inmediatamente
        if metodo_pago == MetodoPago.CREDITO or str(metodo_pago).lower() == "credito":
            continue

        # Resolver billetera
        b = b_item
        if not b and b_id:
            if b_id in billeteras_obj:
                b = billeteras_obj[b_id]
            elif db is not None:
                b = db.get(Billetera, b_id)
                if b:
                    billeteras_obj[b_id] = b

        if not b and billetera_defecto:
            b = billetera_defecto

        if not b:
            continue

        # Verificar si cumple movimiento_anterior_al_saldo
        if movimiento_anterior_al_saldo(b, fecha, metodo_pago):
            b_key = b.id or UUID(int=0)
            billeteras_obj[b_key] = b
            conteo_por_billetera[b_key] = conteo_por_billetera.get(b_key, 0) + 1

    total_movimientos = sum(conteo_por_billetera.values())
    if total_movimientos == 0:
        return None

    cant_billeteras = len(conteo_por_billetera)

    if total_movimientos == 1:
        # 1 movimiento
        b_id = next(iter(conteo_por_billetera))
        b = billeteras_obj[b_id]
        f_pp = fecha_punto_partida(b) or fecha_creacion_billetera(b)
        f_str = formatear_fecha_ddmm(f_pp)
        nom = recortar_nombre_billetera(b.nombre)
        return f"Ojo: es anterior a tu saldo de «{nom}» ({f_str}) y no lo modifica."

    if cant_billeteras == 1:
        # Varios, todos de una misma billetera
        b_id = next(iter(conteo_por_billetera))
        b = billeteras_obj[b_id]
        f_pp = fecha_punto_partida(b) or fecha_creacion_billetera(b)
        f_str = formatear_fecha_ddmm(f_pp)
        nom = recortar_nombre_billetera(b.nombre)
        return f"Ojo: {total_movimientos} son anteriores a tu saldo de «{nom}» ({f_str}) y no lo modifican."

    # Varios de distintas billeteras
    return f"Ojo: {total_movimientos} son anteriores a tu saldo y no lo modifican."


def obtener_aviso_saldo_individual(
    transaccion: Transaccion,
    billetera: Billetera | None = None,
    db: Session | None = None,
) -> str | None:
    """Wrapper para 1 movimiento individual."""
    return obtener_aviso_saldo([transaccion], db=db, billetera_defecto=billetera)


def obtener_aviso_saldo_lote(
    transacciones: Sequence[Transaccion],
    db: Session | None = None,
    billeteras_map: dict[UUID, Billetera] | None = None,
) -> str | None:
    """Wrapper para lote de movimientos."""
    return obtener_aviso_saldo(transacciones, db=db, billeteras_map=billeteras_map)
