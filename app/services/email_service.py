"""
app/services/email_service.py — Envío de emails y códigos de verificación.

Dos caches independientes:
  - _verificacion_cache: códigos para verificar email de usuarios nuevos (15 min, 3 intentos)
  - _recuperacion_cache: códigos para recuperar contraseña (15 min, uso único)
"""

import logging
import random
import smtplib
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)

CODIGO_EXPIRACION_SEGUNDOS = 15 * 60  # 15 minutos
MAX_INTENTOS = 3


@dataclass
class EntradaCodigo:
    codigo: str
    expiracion: float
    intentos_fallidos: int = field(default=0)


_verificacion_cache: dict[str, EntradaCodigo] = {}
_recuperacion_cache: dict[str, EntradaCodigo] = {}


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _limpiar(cache: dict) -> None:
    ahora = time.time()
    expirados = [k for k, v in cache.items() if v.expiracion <= ahora]
    for k in expirados:
        del cache[k]


def _generar_codigo() -> str:
    return f"{random.randint(0, 999999):06d}"


def _enviar_email(destinatario: str, asunto: str, cuerpo: str) -> bool:
    if not settings.SMTP_USER or "email de la app" in settings.SMTP_FROM:
        logger.warning("⚠️ SMTP no configurado — email para %s: %s", destinatario, cuerpo)
        print(f"\n{'='*50}")
        print(f"📧 EMAIL (modo desarrollo)")
        print(f"   Para: {destinatario}")
        print(f"   Asunto: {asunto}")
        print(f"   Cuerpo: {cuerpo}")
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

        logger.info("Email enviado exitosamente a %s", destinatario)
        return True
    except Exception as e:
        logger.error("Error al enviar email a %s: %s", destinatario, e)
        return False


# ---------------------------------------------------------------------------
# Verificación de email (usuarios nuevos)
# ---------------------------------------------------------------------------

def guardar_codigo_verificacion_email(email: str, codigo: str) -> None:
    _limpiar(_verificacion_cache)
    _verificacion_cache[email] = EntradaCodigo(
        codigo=codigo,
        expiracion=time.time() + CODIGO_EXPIRACION_SEGUNDOS,
    )


def verificar_codigo_email(email: str, codigo: str) -> tuple[bool, str | None]:
    """Devuelve (ok, mensaje_error). Si ok=True el código queda invalidado."""
    _limpiar(_verificacion_cache)

    entrada = _verificacion_cache.get(email)
    if not entrada:
        return False, "El código expiró. Pedí uno nuevo."

    if time.time() > entrada.expiracion:
        del _verificacion_cache[email]
        return False, "El código expiró. Pedí uno nuevo."

    if entrada.codigo != codigo:
        entrada.intentos_fallidos += 1
        restantes = MAX_INTENTOS - entrada.intentos_fallidos
        if restantes <= 0:
            del _verificacion_cache[email]
            return False, "Demasiados intentos fallidos. Pedí un código nuevo."
        return False, f"Código incorrecto. Te quedan {restantes} intento{'s' if restantes != 1 else ''}."

    del _verificacion_cache[email]
    return True, None


def enviar_email_verificacion(destinatario: str, codigo: str) -> bool:
    codigo_nuevo = _generar_codigo() if not codigo else codigo
    guardar_codigo_verificacion_email(destinatario, codigo_nuevo)
    asunto = "Verificá tu cuenta en Argentum"
    cuerpo = (
        f"Tu código de verificación es: {codigo_nuevo}\n\n"
        f"Este código expira en 15 minutos.\n"
        f"Si no creaste una cuenta en Argentum, ignorá este mensaje."
    )
    return _enviar_email(destinatario, asunto, cuerpo)


def generar_y_enviar_verificacion_email(destinatario: str) -> str:
    """Genera código, lo guarda y lo envía. Devuelve el código (para logs en dev)."""
    codigo = _generar_codigo()
    guardar_codigo_verificacion_email(destinatario, codigo)
    asunto = "Verificá tu cuenta en Argentum"
    cuerpo = (
        f"Tu código de verificación es: {codigo}\n\n"
        f"Este código expira en 15 minutos.\n"
        f"Si no creaste una cuenta en Argentum, ignorá este mensaje."
    )
    _enviar_email(destinatario, asunto, cuerpo)
    return codigo


# ---------------------------------------------------------------------------
# Recuperación de contraseña
# ---------------------------------------------------------------------------

def generar_codigo_recuperacion() -> str:
    return _generar_codigo()


def guardar_codigo_recuperacion(email: str, codigo: str) -> None:
    _limpiar(_recuperacion_cache)
    _recuperacion_cache[email] = EntradaCodigo(
        codigo=codigo,
        expiracion=time.time() + CODIGO_EXPIRACION_SEGUNDOS,
    )


def verificar_codigo_recuperacion(email: str, codigo: str) -> bool:
    """Uso único — elimina el código si es correcto."""
    _limpiar(_recuperacion_cache)

    entrada = _recuperacion_cache.get(email)
    if not entrada:
        return False

    if time.time() > entrada.expiracion:
        del _recuperacion_cache[email]
        return False

    if entrada.codigo != codigo:
        return False

    del _recuperacion_cache[email]
    return True


def enviar_email_recuperacion(destinatario: str, codigo: str) -> bool:
    asunto = "Tu código de recuperación de Argentum"
    cuerpo = (
        f"Tu código de recuperación es: {codigo}\n\n"
        f"Este código expira en 15 minutos.\n"
        f"Si no pediste recuperar tu contraseña, ignorá este mensaje."
    )
    return _enviar_email(destinatario, asunto, cuerpo)
