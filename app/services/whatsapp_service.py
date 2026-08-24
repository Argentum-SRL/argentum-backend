"""
app/services/whatsapp_service.py — Servicio de mensajería y verificación por WhatsApp con Meta Cloud API.
"""

import logging
import random
import time
from dataclasses import dataclass, field

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

CODIGO_EXPIRACION_SEGUNDOS = 10 * 60  # 10 minutos
MAX_INTENTOS = 3


@dataclass
class EntradaCodigo:
    codigo: str
    expiracion: float
    intentos_fallidos: int = field(default=0)


_codigo_cache: dict[str, EntradaCodigo] = {}


def _limpiar_expirados() -> None:
    ahora = time.time()
    expirados = [k for k, v in _codigo_cache.items() if v.expiracion <= ahora]
    for k in expirados:
        del _codigo_cache[k]


def generar_codigo() -> str:
    return f"{random.randint(0, 999999):06d}"


def guardar_codigo(telefono: str, codigo: str) -> None:
    _limpiar_expirados()
    _codigo_cache[telefono] = EntradaCodigo(
        codigo=codigo,
        expiracion=time.time() + CODIGO_EXPIRACION_SEGUNDOS,
    )


def verificar_codigo(telefono: str, codigo: str) -> tuple[bool, str | None]:
    """
    Verifica el código. Devuelve (ok, mensaje_error).
    Si ok=True el código se invalida (uso único).
    """
    _limpiar_expirados()

    entrada = _codigo_cache.get(telefono)
    if not entrada:
        return False, "El código expiró. Pedí uno nuevo."

    if time.time() > entrada.expiracion:
        del _codigo_cache[telefono]
        return False, "El código expiró. Pedí uno nuevo."

    if entrada.codigo != codigo:
        entrada.intentos_fallidos += 1
        restantes = MAX_INTENTOS - entrada.intentos_fallidos
        if restantes <= 0:
            del _codigo_cache[telefono]
            return False, "Demasiados intentos fallidos. Pedí un código nuevo."
        return False, f"Código incorrecto. Te quedan {restantes} intento{'s' if restantes != 1 else ''}."

    del _codigo_cache[telefono]
    return True, None


def formatear_numero_whatsapp(telefono: str) -> str:
    """
    Formatea un número para WhatsApp Meta Cloud API (formato E.164 plano de solo dígitos, sin prefijo whatsapp:).
    - Remueve 'whatsapp:' si existe
    - Remueve espacios, guiones y caracteres no numéricos
    - Si empieza con '0' (formato local ej. 011...): convierte a '549' + número sin el 0
    - Si empieza con '15' (formato local ej. 15...): convierte a '549' + número
    """
    if not telefono:
        return ""

    tel = telefono.strip()
    if tel.startswith("whatsapp:"):
        tel = tel[9:].strip()

    if tel.startswith("+"):
        tel = tel[1:].strip()
    elif tel.startswith("0"):
        tel = f"549{tel[1:]}"
    elif tel.startswith("15"):
        tel = f"549{tel}"

    digitos = "".join(c for c in tel if c.isdigit())
    return digitos


def enviar_whatsapp(numero: str, mensaje: str) -> bool:
    """
    Envía un mensaje por WhatsApp usando Meta WhatsApp Cloud API (Graph API).
    Incluye 3 reintentos con backoff exponencial ante timeouts o errores 5xx de Meta.
    """
    to_whatsapp = formatear_numero_whatsapp(numero)

    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.warning(
            "WhatsApp / Meta API no configurado; mensaje simulado para %s",
            numero,
        )
        logger.info("[WHATSAPP-DEV] to=%s body=%s", to_whatsapp, mensaje)
        return True

    url = f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_whatsapp,
        "type": "text",
        "text": {"body": mensaje},
    }

    max_intentos = 3
    backoff = 0.5

    for intento in range(1, max_intentos + 1):
        try:
            logger.debug(
                "Enviando WhatsApp vía Meta (intento %d/%d) a %s",
                intento,
                max_intentos,
                to_whatsapp,
            )
            with httpx.Client(timeout=15) as client:
                response = client.post(url, headers=headers, json=payload)

                if response.is_success:
                    res_json = response.json()
                    msg_id = (
                        res_json.get("messages", [{}])[0].get("id", "N/A")
                        if res_json.get("messages")
                        else "N/A"
                    )
                    logger.info(
                        "WhatsApp enviado exitosamente a %s vía Meta. Message ID: %s",
                        to_whatsapp,
                        msg_id,
                    )
                    return True

                # Si es error 4xx de cliente (bad request, auth error, etc.), no reintentar
                if 400 <= response.status_code < 500:
                    logger.error(
                        "Error de cliente al enviar WhatsApp a %s (HTTP %d): %s",
                        to_whatsapp,
                        response.status_code,
                        response.text,
                    )
                    return False

                # Error 5xx del servidor de Meta
                logger.warning(
                    "Error de servidor de Meta al enviar WhatsApp a %s (HTTP %d): %s. Reintentando...",
                    to_whatsapp,
                    response.status_code,
                    response.text,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning(
                "Timeout o error de red al enviar WhatsApp a %s (intento %d/%d): %s",
                to_whatsapp,
                intento,
                max_intentos,
                exc,
            )
        except Exception as exc:
            logger.error("Error inesperado al enviar WhatsApp a %s: %s", to_whatsapp, exc)
            return False

        if intento < max_intentos:
            time.sleep(backoff)
            backoff *= 2

    logger.error("Fallaron todos los intentos (%d) para enviar WhatsApp a %s", max_intentos, to_whatsapp)
    return False


def enviar_mensaje_whatsapp(telefono: str, mensaje: str) -> bool:
    """Alias de compatibilidad."""
    return enviar_whatsapp(telefono, mensaje)
