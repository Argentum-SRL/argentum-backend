"""
app/services/sms_service.py — Servicio de verificación por SMS con Twilio.

Cache en memoria para códigos de verificación (dict con TTL de 10 minutos).
Máximo 3 intentos fallidos; después el código se invalida y hay que pedir uno nuevo.
"""

import logging
import random
import time
from dataclasses import dataclass, field

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

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


def enviar_sms(telefono: str, codigo: str) -> bool:
    """
    Envía el código de verificación por SMS usando Twilio.
    En desarrollo sin credenciales, loguea en consola.
    """
    mensaje = f"Tu código de verificación de Argentum es: {codigo}"

    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning(
            "⚠️  Twilio no configurado — código de verificación para %s: %s",
            telefono, codigo,
        )
        print(f"\n{'='*50}")
        print(f"📱 CÓDIGO SMS (modo desarrollo)")
        print(f"   Teléfono: {telefono}")
        print(f"   Código:   {codigo}")
        print(f"{'='*50}\n")
        return True

    try:
        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=mensaje,
            from_=settings.TWILIO_WHATSAPP_NUMBER,
            to=telefono,
        )
        logger.info("SMS enviado exitosamente a %s", telefono)
        return True
    except TwilioRestException as e:
        logger.error("Error al enviar SMS a %s: %s", telefono, e)
        return False
