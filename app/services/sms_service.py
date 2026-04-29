"""
app/services/sms_service.py — Servicio de verificación por SMS con Twilio.

Cache en memoria para códigos de verificación (dict con TTL de 10 minutos).
En producción se recomendaría usar Redis, pero para el MVP esto es suficiente.
"""

import logging
import random
import time

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache en memoria: { telefono: (codigo, timestamp_expiracion) }
# ---------------------------------------------------------------------------

_codigo_cache: dict[str, tuple[str, float]] = {}

CODIGO_EXPIRACION_SEGUNDOS = 10 * 60  # 10 minutos


def _limpiar_expirados() -> None:
    """Elimina entradas expiradas del cache (housekeeping ligero)."""
    ahora = time.time()
    expirados = [tel for tel, (_, exp) in _codigo_cache.items() if exp <= ahora]
    for tel in expirados:
        del _codigo_cache[tel]


def generar_codigo() -> str:
    """Genera un código numérico de 6 dígitos aleatorio."""
    return f"{random.randint(0, 999999):06d}"


def guardar_codigo(telefono: str, codigo: str) -> None:
    """Guarda el código en el cache con expiración de 10 minutos."""
    _limpiar_expirados()
    expiracion = time.time() + CODIGO_EXPIRACION_SEGUNDOS
    _codigo_cache[telefono] = (codigo, expiracion)


def verificar_codigo(telefono: str, codigo: str) -> bool:
    """
    Verifica que el código sea correcto y no haya expirado.
    Si es correcto, lo elimina del cache (uso único).
    """
    _limpiar_expirados()

    if telefono not in _codigo_cache:
        return False

    codigo_guardado, expiracion = _codigo_cache[telefono]

    if time.time() > expiracion:
        del _codigo_cache[telefono]
        return False

    if codigo_guardado != codigo:
        return False

    # Código correcto → eliminarlo (uso único)
    del _codigo_cache[telefono]
    return True


def enviar_sms(telefono: str, codigo: str) -> bool:
    """
    Envía el código de verificación por SMS usando Twilio.

    En modo development sin credenciales configuradas,
    loguea el código en consola en lugar de enviar el SMS real.
    Esto permite testear el flujo completo sin gastar créditos.
    """
    mensaje = f"Tu código de verificación de Argentum es: {codigo}"

    # Si no hay credenciales de Twilio configuradas, simular el envío
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning(
            "⚠️  Twilio no configurado — código de verificación para %s: %s",
            telefono, codigo,
        )
        print(f"\n{'='*50}")
        print(f"📱 CÓDIGO DE VERIFICACIÓN (modo desarrollo)")
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
