"""
app/services/whatsapp_service.py — Servicio de verificación por WhatsApp con Twilio Sandbox.
"""

import logging
import random
import time
from dataclasses import dataclass, field

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

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
_twilio_client: Client | None = None


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
    Formatea un número para Twilio WhatsApp.
    - Si ya tiene 'whatsapp:': devolver como esta
    - Si empieza con '+': agregar 'whatsapp:' adelante
    - Si empieza con '0': reemplazar '0' por 'whatsapp:+549'
    - Si empieza con '15': agregar 'whatsapp:+549' adelante
    """
    if telefono.startswith("whatsapp:"):
        return telefono
    if telefono.startswith("+"):
        return f"whatsapp:{telefono}"
    if telefono.startswith("0"):
        return f"whatsapp:+549{telefono[1:]}"
    if telefono.startswith("15"):
        return f"whatsapp:+549{telefono}"
    
    # Caso por defecto si no cumple ninguno de los anteriores pero queremos ser seguros
    return f"whatsapp:{telefono}"


def _from_whatsapp() -> str:
    if settings.TWILIO_WHATSAPP_FROM:
        return settings.TWILIO_WHATSAPP_FROM

    if settings.TWILIO_WHATSAPP_NUMBER:
        numero = settings.TWILIO_WHATSAPP_NUMBER
        return numero if numero.startswith("whatsapp:") else f"whatsapp:{numero}"

    return "whatsapp:+14155238886"


def _get_twilio_client() -> Client | None:
    global _twilio_client

    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        return None

    if _twilio_client is None:
        _twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    return _twilio_client


def enviar_whatsapp(numero: str, mensaje: str) -> bool:
    """
    Envía un mensaje por WhatsApp usando Twilio.
    - Usar formatear_numero_whatsapp() para el to
    - Usar 'whatsapp:+14155238886' como from_
    - Manejar errores de Twilio con try/except
    - Devolver True si se envio, False si fallo
    """
    to_whatsapp = formatear_numero_whatsapp(numero)
    from_whatsapp = _from_whatsapp()
    client = _get_twilio_client()

    if client is None:
        logger.warning(
            "Twilio no configurado; mensaje de WhatsApp simulado para %s",
            numero,
        )
        logger.info("[WHATSAPP-DEV] from=%s to=%s body=%s", from_whatsapp, to_whatsapp, mensaje)
        return True

    try:
        client.messages.create(
            body=mensaje,
            from_=from_whatsapp,
            to=to_whatsapp,
        )
        logger.info("WhatsApp enviado exitosamente a %s", numero)
        return True
    except TwilioRestException as e:
        logger.error("Error al enviar WhatsApp a %s: %s", numero, e)
        return False


def enviar_mensaje_whatsapp(telefono: str, mensaje: str) -> bool:
    """Alias de compatibilidad."""
    return enviar_whatsapp(telefono, mensaje)
