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
from app.services.email_templates import (
    template_verificacion_email,
    template_recupero_contrasena,
    template_reset_password_email,
)

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


def _obtener_nombre_usuario(email: str) -> str:
    try:
        from app.core.database import SessionLocal
        from app.models.usuario import Usuario
        from sqlalchemy import select
        with SessionLocal() as db:
            user = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()
            if user and user.nombre:
                return user.nombre
    except Exception as e:
        logger.warning("No se pudo obtener el nombre del usuario desde la base de datos: %s", e)
    return "Usuario"


def _enviar_email(destinatario: str, asunto: str, cuerpo: str, cuerpo_html: str = None) -> bool:
    if not settings.SMTP_USER or "email de la app" in settings.SMTP_FROM:
        logger.warning("⚠️ SMTP no configurado — email para %s: %s", destinatario, cuerpo)
        logger.info("EMAIL (modo desarrollo) para=%s asunto=%s cuerpo=%s", destinatario, asunto, cuerpo)
        return True

    try:
        if cuerpo_html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
            msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))
        else:
            msg = MIMEMultipart()
            msg.attach(MIMEText(cuerpo, "plain", "utf-8"))

        msg["From"] = settings.SMTP_FROM
        msg["To"] = destinatario
        msg["Subject"] = asunto

        server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()

        logger.info("Email enviado exitosamente a %s", destinatario)
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(
            "SMTPAuthenticationError enviando email a %s. Gmail suele requerir App Password o 2FA habilitado: %s",
            destinatario,
            e,
        )
        return False
    except Exception as e:
        logger.exception("Error crítico al enviar email a %s", destinatario)
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
    """
    Verifica el código de email. Devuelve (ok, mensaje_error).
    Si ok=True el código se invalida (uso único).
    """
    _limpiar(_verificacion_cache)

    entrada = _verificacion_cache.get(email)
    if not entrada:
        return False, "El código ya expiró. Pedí uno nuevo."

    if time.time() > entrada.expiracion:
        del _verificacion_cache[email]
        return False, "El código ya expiró. Pedí uno nuevo."

    if entrada.codigo != codigo:
        entrada.intentos_fallidos += 1
        restantes = MAX_INTENTOS - entrada.intentos_fallidos
        if restantes <= 0:
            del _verificacion_cache[email]
            return False, "Demasiados intentos fallidos. Pedí un código nuevo."
        return False, f"Código incorrecto. Te quedan {restantes} intento{'s' if restantes != 1 else ''}."

    # Éxito: borrar de la memoria y devolver OK
    del _verificacion_cache[email]
    return True, None


def enviar_email_verificacion(destinatario: str, codigo: str) -> bool:
    codigo_nuevo = _generar_codigo() if not codigo else codigo
    guardar_codigo_verificacion_email(destinatario, codigo_nuevo)
    asunto = "Verificá tu email para entrar a Argentum"
    link = f"{settings.BACKEND_URL}/auth/email/verificar-link?email={destinatario}&codigo={codigo_nuevo}"
    cuerpo = (
        f"Tu código de verificación es: {codigo_nuevo}\n\n"
        f"O haz clic en el siguiente enlace para verificar tu cuenta directamente:\n"
        f"{link}\n\n"
        f"Este código expira en 15 minutos.\n"
        f"Si no creaste una cuenta en Argentum, ignorá este mensaje."
    )
    nombre = _obtener_nombre_usuario(destinatario)
    cuerpo_html = template_verificacion_email(nombre=nombre, codigo=codigo_nuevo)
    return _enviar_email(destinatario, asunto, cuerpo, cuerpo_html)


def generar_y_enviar_verificacion_email(destinatario: str) -> str:
    """Genera código, lo guarda y lo envía. Devuelve el código (para logs en dev)."""
    codigo = _generar_codigo()
    guardar_codigo_verificacion_email(destinatario, codigo)
    asunto = "Verificá tu email para entrar a Argentum"
    link = f"{settings.BACKEND_URL}/auth/email/verificar-link?email={destinatario}&codigo={codigo}"
    cuerpo = (
        f"Tu código de verificación es: {codigo}\n\n"
        f"O haz clic en el siguiente enlace para verificar tu cuenta directamente:\n"
        f"{link}\n\n"
        f"Este código expira en 15 minutos.\n"
        f"Si no creaste una cuenta en Argentum, ignorá este mensaje."
    )
    nombre = _obtener_nombre_usuario(destinatario)
    cuerpo_html = template_verificacion_email(nombre=nombre, codigo=codigo)
    enviado = _enviar_email(destinatario, asunto, cuerpo, cuerpo_html)
    if not enviado:
        logger.warning(
            "Email de verificación no enviado a %s. La cuenta se creó igual y el código quedó guardado en memoria.",
            destinatario,
        )
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
    """
    Verifica el código de recuperación.
    Uso único: si es correcto, se borra.
    """
    from fastapi import HTTPException

    _limpiar(_recuperacion_cache)

    entrada = _recuperacion_cache.get(email)
    if not entrada:
        return False

    if time.time() > entrada.expiracion:
        del _recuperacion_cache[email]
        return False

    if entrada.codigo != codigo:
        entrada.intentos_fallidos += 1
        if entrada.intentos_fallidos >= MAX_INTENTOS:
            del _recuperacion_cache[email]
            raise HTTPException(
                status_code=400,
                detail="El código de verificación ya no es válido. Por favor, solicitá un código nuevo."
            )
        return False

    # Éxito: borrar y devolver OK
    del _recuperacion_cache[email]
    return True


def enviar_email_recuperacion(destinatario: str, codigo: str) -> bool:
    asunto = "Tu código de recuperación de Argentum"
    cuerpo = (
        f"Tu código de recuperación es: {codigo}\n\n"
        f"Este código expira en 15 minutos.\n"
        f"Si no pediste recuperar tu contraseña, ignorá este mensaje."
    )
    nombre = _obtener_nombre_usuario(destinatario)
    link_recupero = f"{settings.FRONTEND_URL}/auth/recuperar-password?email={destinatario}&codigo={codigo}"
    cuerpo_html = template_recupero_contrasena(nombre=nombre, link=link_recupero)
    return _enviar_email(destinatario, asunto, cuerpo, cuerpo_html)


def enviar_reset_password_email(email: str, nombre: str, reset_url: str) -> bool:
    asunto = "Restablecé tu contraseña en Argentum"
    cuerpo = (
        f"Hola {nombre},\n\n"
        f"Un administrador ha solicitado el restablecimiento de tu contraseña en Argentum.\n\n"
        f"Podés restablecer tu contraseña haciendo clic en el siguiente enlace:\n"
        f"{reset_url}\n\n"
        f"Este enlace expira en 1 hora.\n"
        f"Si vos no solicitaste esto, podés ignorar este correo de forma segura."
    )
    cuerpo_html = template_reset_password_email(nombre=nombre, reset_url=reset_url)
    return _enviar_email(email, asunto, cuerpo, cuerpo_html)


def enviar_email_aviso_google(destinatario: str) -> bool:
    asunto = "Acceso a tu cuenta de Argentum con Google"
    nombre = _obtener_nombre_usuario(destinatario)
    login_url = f"{settings.FRONTEND_URL}/login"
    cuerpo = (
        f"Hola {nombre},\n\n"
        f"Recibimos una solicitud para recuperar la contraseña de tu cuenta en Argentum.\n\n"
        f"Tu cuenta está vinculada a Google OAuth, por lo que no necesitás una contraseña local. "
        f"Podés iniciar sesión directamente haciendo clic en el botón 'Continuar con Google' en la pantalla de inicio de sesión:\n"
        f"{login_url}\n\n"
        f"Si no realizaste esta solicitud, podés ignorar este mensaje de forma segura."
    )
    cuerpo_html = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 32px 24px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 16px;">
        <h2 style="color: #0f172a; margin-top: 0; font-size: 22px; font-weight: 700;">Acceso mediante Google en Argentum</h2>
        <p style="color: #475569; font-size: 15px; line-height: 1.6;">Hola <strong>{nombre}</strong>,</p>
        <p style="color: #475569; font-size: 15px; line-height: 1.6;">Recibimos una solicitud para restablecer la contraseña de tu cuenta.</p>
        <div style="background-color: #f8fafc; border-left: 4px solid #4285f4; padding: 16px; border-radius: 8px; margin: 20px 0;">
            <p style="color: #1e293b; font-size: 14px; margin: 0; font-weight: 500;">
                Tu cuenta utiliza autenticación directa con <strong>Google OAuth</strong>, por lo que no utiliza una contraseña local en Argentum.
            </p>
        </div>
        <p style="color: #475569; font-size: 15px; line-height: 1.6;">Podés ingresar a tu cuenta directamente tocando el botón de Google en el inicio de sesión:</p>
        <div style="text-align: center; margin: 32px 0;">
            <a href="{login_url}" style="background-color: #0f172a; color: #ffffff; text-decoration: none; padding: 14px 28px; border-radius: 10px; font-weight: 600; font-size: 15px; display: inline-block;">Iniciar sesión con Google</a>
        </div>
        <p style="color: #94a3b8; font-size: 13px; line-height: 1.5; margin-top: 32px; border-top: 1px solid #f1f5f9; padding-top: 16px;">
            Si no fuiste vos quien solicitó esto, podés ignorar este correo con total tranquilidad.
        </p>
    </div>
    """
    return _enviar_email(destinatario, asunto, cuerpo, cuerpo_html)
