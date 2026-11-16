"""
app/routers/whatsapp/permitirse_flujo.py

Pregunta pendiente de '¿Me lo puedo permitir?' por WhatsApp (bloque T1): cuando falta el
precio o la cantidad de cuotas, la conversación queda abierta y el próximo mensaje, si es un
monto o "N cuotas", completa la consulta. Si es otra cosa, la pregunta se cierra y el mensaje
sigue su camino normal.
"""
from __future__ import annotations

from decimal import Decimal
import re
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.conversacion_wpp import ConversacionWpp
from app.routers.whatsapp.db_lookups import _buscar_slot_filling_activo
from app.routers.whatsapp.montos_palabras import buscar_monto
from app.utils.texto import normalizar_texto

INTENT_ORIGEN = "puede_permitirse"

# Palabras que pueden acompañar al monto en la respuesta sin cambiar lo que dice
RELLENO = {"sale", "salen", "cuesta", "cuestan", "son", "es", "unos", "unas", "como", "pesos", "de"}
PALABRAS_DOLAR = {"dolar", "dolares", "usd", "us", "u$s", "us$"}


def armar_estado_pregunta(analisis: dict) -> dict[str, Any]:
    """Estado que se guarda con la pregunta: qué falta y lo que ya se entendió."""
    precio = analisis.get("precio")
    tna = analisis.get("tna")
    return {
        "intent_origen": INTENT_ORIGEN,
        "paso": analisis.get("falta"),
        "precio": str(precio) if precio is not None else None,
        "modo": analisis.get("modo") or "contado",
        "cantidad_cuotas": analisis.get("cantidad_cuotas"),
        "tiene_interes": bool(analisis.get("tiene_interes")),
        "tna": str(tna) if tna is not None else None,
        "nota_interes": bool(analisis.get("nota_interes")),
        "moneda": analisis.get("moneda") or "ARS",
    }


def buscar_pregunta_pendiente(db: Session, usuario_id: UUID) -> ConversacionWpp | None:
    """La pregunta de '¿Me lo puedo permitir?' que sigue abierta para esa persona, si hay."""
    conv = _buscar_slot_filling_activo(usuario_id, db)
    if conv is None or not isinstance(conv.slot_filling_estado, dict):
        return None
    if conv.slot_filling_estado.get("intent_origen") != INTENT_ORIGEN:
        return None
    return conv


def _que_falta(consulta: dict) -> str | None:
    """Qué dato sigue faltando después de sumar la respuesta."""
    if consulta.get("precio") is None:
        return "precio"
    if consulta.get("modo") == "cuotas" and consulta.get("cantidad_cuotas") is None:
        return "cuotas"
    return None


def leer_respuesta_pendiente(texto: str, estado: dict) -> dict | None:
    """
    Lee la respuesta a la pregunta pendiente. Devuelve la consulta con el dato sumado
    (puede seguir faltando otro) o None si el mensaje no es un monto ni "N cuotas".
    """
    norm = normalizar_texto(texto).strip(" ?¿!¡.")
    if not norm:
        return None

    precio_guardado = estado.get("precio")
    tna_guardada = estado.get("tna")
    consulta: dict[str, Any] = {
        "precio": Decimal(precio_guardado) if precio_guardado else None,
        "modo": estado.get("modo") or "contado",
        "cantidad_cuotas": estado.get("cantidad_cuotas"),
        "tiene_interes": bool(estado.get("tiene_interes")),
        "tna": Decimal(tna_guardada) if tna_guardada else None,
        "nota_interes": bool(estado.get("nota_interes")),
        "moneda": estado.get("moneda") or "ARS",
    }

    paso = estado.get("paso")
    if paso == "precio":
        resto = norm
        cuotas = re.search(r"\b(?:en\s+)?(\d{1,3})\s+cuotas?\b", resto)
        if cuotas:
            resto = (resto[:cuotas.start()] + " " + resto[cuotas.end():]).strip()
        hay_marca_usd = bool(re.search(r"(?:us\$|u\$s|\busd\b|\bdolares?\b)", resto))
        resto_limpio = re.sub(r"(?:us\$|u\$s)\s*", "", resto)
        monto, sobrante = buscar_monto(resto_limpio)
        if monto is None or monto <= 0:
            return None
        palabras = {p.strip(" ?¿!¡.,$") for p in sobrante.split()} - {""}
        if hay_marca_usd or (palabras & PALABRAS_DOLAR):
            consulta["moneda"] = "USD"
        if palabras - RELLENO - PALABRAS_DOLAR:
            return None
        consulta["precio"] = Decimal(str(monto))
        if cuotas:
            consulta["modo"] = "cuotas"
            consulta["cantidad_cuotas"] = int(cuotas.group(1))
    elif paso == "cuotas":
        cantidad = re.fullmatch(r"(?:en\s+)?(\d{1,3})(?:\s+cuotas?)?", norm)
        if not cantidad or not (1 <= int(cantidad.group(1)) <= 120):
            return None
        consulta["modo"] = "cuotas"
        consulta["cantidad_cuotas"] = int(cantidad.group(1))
    else:
        return None

    if consulta["modo"] != "cuotas":
        consulta["cantidad_cuotas"] = 1
    consulta["falta"] = _que_falta(consulta)
    return consulta
