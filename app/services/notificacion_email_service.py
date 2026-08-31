import httpx
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.core.config import settings
from app.services.email_templates import (
    template_cambio_contrasena,
    template_nuevo_dispositivo,
    template_intentos_login,
)

logger = logging.getLogger(__name__)


def enviar_email_notificacion(
    destinatario_email: str,
    asunto: str,
    cuerpo_html: str,
    cuerpo_texto: str,
) -> bool:
    """
    Envía un email de notificación vía Resend API. Retorna True si exitoso, False si falla.
    SIEMPRE usa timeout=10 para evitar bloqueos del worker.
    """
    if not settings.RESEND_API_KEY:
        logger.warning("⚠️ RESEND_API_KEY no configurada — email de notificación para %s: %s", destinatario_email, cuerpo_texto)
        logger.info("EMAIL NOTIFICACIÓN (modo desarrollo) para=%s asunto=%s cuerpo=%s", destinatario_email, asunto, cuerpo_texto)
        return True

    payload = {
        "from": "Argentum <no-responder@miargentum.com>",
        "to": [destinatario_email],
        "subject": asunto,
        "html": cuerpo_html,
        "text": cuerpo_texto,
    }

    try:
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10.0,
        )
        if response.status_code >= 400:
            logger.error(
                "Error en API de Resend al enviar email de notificación a %s (status %s): %s",
                destinatario_email,
                response.status_code,
                response.text,
            )
            return False

        logger.info("Email de notificación enviado exitosamente a %s vía Resend", destinatario_email)
        return True
    except httpx.TimeoutException:
        logger.error("Timeout enviando email de notificación a %s vía Resend", destinatario_email)
        return False
    except Exception as e:
        logger.error("Error inesperado enviando email de notificación a %s vía Resend: %s", destinatario_email, e)
        return False


from app.utils.fecha import ahora_argentina


def generar_email_cambio_contrasena(
    usuario_nombre: str,
    fecha_hora_argentina: str = None,
    dispositivo: str = "No especificado"
) -> tuple[str, str, str]:
    if not fecha_hora_argentina:
        fecha_hora_argentina = ahora_argentina().strftime("%d/%m/%Y a las %H:%M")
    
    asunto = "Argentum — Cambio de contraseña detectado"
    html = template_cambio_contrasena(
        nombre=usuario_nombre,
        fecha_hora_argentina=fecha_hora_argentina,
        dispositivo=dispositivo
    )
    texto = (
        f"Hola {usuario_nombre},\n\n"
        f"Se cambió la contraseña de tu cuenta el {fecha_hora_argentina} (hora Argentina).\n\n"
        f"Si no fuiste vos, contactanos de inmediato respondiendo este email o cambiá tu contraseña de emergencia."
    )
    return asunto, html, texto


def generar_email_nuevo_dispositivo(
    usuario_nombre: str,
    ip: str = None,
    dispositivo: str = "No especificado",
    ubicacion: str = "No especificada",
    link_bloqueo: str = None
) -> tuple[str, str, str]:
    ts = ahora_argentina().strftime("%d/%m/%Y a las %H:%M")
    ip_info = f" desde la IP {ip}" if ip else ""
    
    if ip and dispositivo == "No especificado":
        dispositivo = f"Dispositivo (IP: {ip})"
        
    if not link_bloqueo:
        link_bloqueo = f"{settings.FRONTEND_URL}/auth/recuperar-password"
        
    asunto = "Argentum — Inicio de sesión desde un nuevo dispositivo"
    html = template_nuevo_dispositivo(
        nombre=usuario_nombre,
        fecha_hora_argentina=ts,
        dispositivo=dispositivo,
        ubicacion=ubicacion,
        link_bloqueo=link_bloqueo
    )
    texto = (
        f"Hola {usuario_nombre},\n\n"
        f"Nuevo inicio de sesión{ip_info} el {ts} (hora Argentina).\n\n"
        f"Si no fuiste vos, cambiá tu contraseña."
    )
    return asunto, html, texto


def generar_email_intentos_login(
    usuario_nombre: str,
    cantidad_intentos: int = 5,
    fecha_hora_argentina: str = None,
    link_recupero: str = None
) -> tuple[str, str, str]:
    if not fecha_hora_argentina:
        fecha_hora_argentina = ahora_argentina().strftime("%d/%m/%Y a las %H:%M")
        
    if not link_recupero:
        link_recupero = f"{settings.FRONTEND_URL}/auth/recuperar-password"
        
    asunto = "Argentum — Múltiples intentos de acceso fallidos"
    html = template_intentos_login(
        nombre=usuario_nombre,
        cantidad_intentos=cantidad_intentos,
        fecha_hora_argentina=fecha_hora_argentina,
        link_recupero=link_recupero
    )
    texto = (
        f"Hola {usuario_nombre},\n\n"
        f"Detectamos múltiples intentos fallidos de acceso a tu cuenta el {fecha_hora_argentina}. Si no fuiste vos, cambiá tu contraseña."
    )
    return asunto, html, texto
