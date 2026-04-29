"""
app/services/email_service.py — Servicio de envío de emails y gestión de códigos de recuperación.
"""

import logging
import random
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)

# Cache en memoria: { email: (codigo, timestamp_expiracion) }
_recuperacion_cache: dict[str, tuple[str, float]] = {}

CODIGO_EXPIRACION_SEGUNDOS = 15 * 60  # 15 minutos


def _limpiar_expirados() -> None:
    """Elimina entradas expiradas del cache."""
    ahora = time.time()
    expirados = [email for email, (_, exp) in _recuperacion_cache.items() if exp <= ahora]
    for email in expirados:
        del _recuperacion_cache[email]


def generar_codigo_recuperacion() -> str:
    """Genera un código numérico de 6 dígitos aleatorio."""
    return f"{random.randint(0, 999999):06d}"


def guardar_codigo_recuperacion(email: str, codigo: str) -> None:
    """Guarda el código en el cache con expiración de 15 minutos."""
    _limpiar_expirados()
    expiracion = time.time() + CODIGO_EXPIRACION_SEGUNDOS
    _recuperacion_cache[email] = (codigo, expiracion)


def verificar_codigo_recuperacion(email: str, codigo: str) -> bool:
    """
    Verifica que el código sea correcto y no haya expirado.
    Si es correcto, lo elimina del cache.
    """
    _limpiar_expirados()

    if email not in _recuperacion_cache:
        return False

    codigo_guardado, expiracion = _recuperacion_cache[email]

    if time.time() > expiracion:
        del _recuperacion_cache[email]
        return False

    if codigo_guardado != codigo:
        return False

    # Código correcto → eliminarlo (uso único)
    del _recuperacion_cache[email]
    return True


def enviar_email_recuperacion(destinatario: str, codigo: str) -> bool:
    """
    Envía el código de recuperación por email usando SMTP.
    """
    asunto = "Tu código de Argentum"
    cuerpo = f"Tu código de recuperación es: {codigo}\n\nEste código expira en 15 minutos."

    # Si no hay credenciales SMTP configuradas (o son las por defecto), simular el envío
    if not settings.SMTP_USER or "email de la app" in settings.SMTP_USER:
        logger.warning(
            "⚠️ SMTP no configurado — código de recuperación para %s: %s",
            destinatario, codigo,
        )
        print(f"\n{'='*50}")
        print(f"📧 EMAIL DE RECUPERACIÓN (modo desarrollo)")
        print(f"   Para: {destinatario}")
        print(f"   Código: {codigo}")
        print(f"{'='*50}\n")
        return True

    try:
        msg = MIMEMultipart()
        msg["From"] = settings.SMTP_FROM
        msg["To"] = destinatario
        msg["Subject"] = asunto
        msg.attach(MIMEText(cuerpo, "plain"))

        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        logger.info("Email de recuperación enviado exitosamente a %s", destinatario)
        return True
    except Exception as e:
        logger.error("Error al enviar email a %s: %s", destinatario, e)
        return False
