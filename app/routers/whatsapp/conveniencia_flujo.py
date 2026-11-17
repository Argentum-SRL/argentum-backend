# -*- coding: utf-8 -*-
"""
app/routers/whatsapp/conveniencia_flujo.py

Pregunta pendiente de 'Cuotas vs contado' por WhatsApp (bloque T2): cuando falta el
valor de cada cuota o si son sin interés, la conversación queda abierta y el próximo
mensaje completa la consulta o la cierra si es otra cosa.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.conversacion_wpp import ConversacionWpp
from app.routers.whatsapp.db_lookups import _buscar_slot_filling_activo
from app.routers.whatsapp.montos_palabras import buscar_monto
from app.utils.texto import normalizar_texto

INTENT_ORIGEN = "cuotas_vs_contado"

RELLENO_CUOTA = {
    "de", "son", "es", "cada", "una", "cuota", "por", "mes", "pesos",
}


def armar_estado_pregunta(analisis: dict) -> dict[str, Any]:
    """Estado que se guarda con la pregunta pendiente de cuota."""
    contado = analisis.get("precio_contado")
    return {
        "intent_origen": INTENT_ORIGEN,
        "paso": "cuota",
        "precio_contado": str(contado) if contado is not None else None,
        "cantidad_cuotas": analisis.get("cantidad_cuotas"),
    }


def buscar_pregunta_pendiente(db: Session, usuario_id: UUID) -> ConversacionWpp | None:
    """Devuelve la pregunta abierta de 'Cuotas vs contado' para esa persona, si hay."""
    conv = _buscar_slot_filling_activo(usuario_id, db)
    if conv is None or not isinstance(conv.slot_filling_estado, dict):
        return None
    if conv.slot_filling_estado.get("intent_origen") != INTENT_ORIGEN:
        return None
    return conv


def leer_respuesta_pendiente(texto: str, estado: dict) -> dict | None:
    """
    Lee la respuesta a la pregunta pendiente de valor de cuota.
    Solo completa si es un monto, 'de N', 'N cada una' o 'sin interés'.
    """
    if estado.get("paso") != "cuota":
        return None

    norm = normalizar_texto(texto).strip(" ?¿!¡.")
    if not norm:
        return None

    contado_raw = estado.get("precio_contado")
    cant_raw = estado.get("cantidad_cuotas")
    if not contado_raw or not cant_raw:
        return None

    precio_contado = Decimal(str(contado_raw))
    cantidad = int(cant_raw)

    if re.fullmatch(r"(?:son\s+|cuotas\s+)?sin\s+interes", norm):
        valor_cuota = (precio_contado / Decimal(cantidad)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return {
            "precio_contado": precio_contado,
            "cantidad_cuotas": cantidad,
            "valor_cuota": valor_cuota,
            "sin_interes": True,
            "falta": None,
        }

    monto, sobrante = buscar_monto(norm)
    if monto is None or monto <= 0:
        return None

    palabras = {p.strip(" ?¿!¡.,$") for p in sobrante.split()} - {""}
    if palabras - RELLENO_CUOTA:
        return None

    return {
        "precio_contado": precio_contado,
        "cantidad_cuotas": cantidad,
        "valor_cuota": Decimal(str(monto)),
        "sin_interes": False,
        "falta": None,
    }
