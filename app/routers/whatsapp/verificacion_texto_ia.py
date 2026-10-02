"""
Módulo de verificación de textos generados por la IA (Paso fase3_f1).
Asegura que ningún número escrito por la IA llegue al usuario sin verificar.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import structlog

from app.routers.whatsapp.parsers import _parsear_monto_argentino, montos_de_dinero_en_texto

logger = structlog.get_logger(__name__)

TEXTO_IA_NO_VERIFICADO = "No pude armar bien la respuesta. ¿Me lo decís de otra forma?"

# Patrón para identificar números en el texto (incluyendo montos con separadores y signo $)
_PATRON_NUMEROS = re.compile(r"(?<!\w)\$?\s*\d+(?:[\.,]\d+)*(?!\w)")


class TextoIA(str):
    """Texto escrito por la IA, todavía sin verificar."""
    pass


def numeros_del_texto(texto: str) -> set[Decimal]:
    """
    Extrae todos los números presentes en el texto y los convierte con _parsear_monto_argentino.
    No cuenta los marcadores de lista al principio de una línea ("1.", "2)").
    """
    if not texto or not isinstance(texto, str):
        return set()

    # Ignorar marcadores de lista al inicio de línea ("1.", "2)", etc.)
    texto_sin_listas = re.sub(r"(?m)^\s*\d+[\.\)](?=\s|$)", "", texto)

    numeros: set[Decimal] = set()
    for m in _PATRON_NUMEROS.finditer(texto_sin_listas):
        token = m.group(0)
        val = _parsear_monto_argentino(token)
        if val is not None:
            numeros.add(val)
        else:
            token_limpio = token.strip().replace("$", "").strip()
            if token_limpio in ("0", "0.0", "0,0", "0.00", "0,00"):
                numeros.add(Decimal("0"))

    return numeros


def verificar_texto_ia(
    texto: Any,
    mensaje_usuario: str | None,
    intent: str | None = None,
) -> str:
    """
    Verifica los números en un texto generado por la IA antes de enviarlo al usuario.
    - Si texto no es TextoIA, devuelve exactamente el mismo objeto, sin tocarlo.
    - Si es TextoIA, arma los números permitidos combinando montos_de_dinero_en_texto
      y numeros_del_texto del mensaje del usuario.
    - Si todos los números de numeros_del_texto(texto) están entre los permitidos, devuelve str(texto).
    - Si no, registra un warning y devuelve TEXTO_IA_NO_VERIFICADO.
    """
    if not isinstance(texto, TextoIA):
        return texto

    numeros_ia = numeros_del_texto(texto)

    permitidos: set[Decimal] = set()
    if mensaje_usuario:
        for m in montos_de_dinero_en_texto(mensaje_usuario):
            val = _parsear_monto_argentino(m)
            if val is not None:
                permitidos.add(val)
            else:
                m_limpio = m.strip().replace("$", "").strip()
                if m_limpio in ("0", "0.0", "0,0", "0.00", "0,00"):
                    permitidos.add(Decimal("0"))
        permitidos.update(numeros_del_texto(mensaje_usuario))

    if numeros_ia.issubset(permitidos):
        return str(texto)

    logger.warning(
        "texto_ia_reemplazado",
        intent=intent,
        cantidad_numeros=len(numeros_ia),
    )
    return TEXTO_IA_NO_VERIFICADO
