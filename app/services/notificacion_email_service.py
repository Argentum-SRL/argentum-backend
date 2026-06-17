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
    Envía un email de notificación. Retorna True si exitoso, False si falla.
    SIEMPRE usa timeout=10 para evitar bloqueos del worker.
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = f"Argentum <{settings.SMTP_USER}>"
        msg["To"] = destinatario_email

        msg.attach(MIMEText(cuerpo_texto, "plain", "utf-8"))
        msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))

        # timeout=10 es OBLIGATORIO — sin esto el worker puede quedar bloqueado indefinidamente
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_USER, destinatario_email, msg.as_string())

        logger.info("Email de notificación enviado a %s, asunto: %s", destinatario_email, asunto)
        return True
    except smtplib.SMTPException as e:
        logger.error("Error SMTP enviando a %s: %s", destinatario_email, e)
        return False
    except TimeoutError:
        logger.error("Timeout enviando email a %s", destinatario_email)
        return False
    except Exception as e:
        logger.error("Error inesperado enviando email a %s: %s", destinatario_email, e)
        return False


def generar_email_cambio_contrasena(
    usuario_nombre: str,
    fecha_hora_argentina: str = None,
    dispositivo: str = "No especificado"
) -> tuple[str, str, str]:
    from datetime import datetime, timedelta
    if not fecha_hora_argentina:
        # CORRECCIÓN DEL BUG: Usar + timedelta(hours=-3) para restar 3 horas (GMT-3)
        fecha_hora_argentina = (datetime.utcnow() + timedelta(hours=-3)).strftime("%d/%m/%Y a las %H:%M")
    
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
    from datetime import datetime, timedelta
    ts = (datetime.utcnow() + timedelta(hours=-3)).strftime("%d/%m/%Y a las %H:%M")
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
    from datetime import datetime, timedelta
    if not fecha_hora_argentina:
        fecha_hora_argentina = (datetime.utcnow() + timedelta(hours=-3)).strftime("%d/%m/%Y a las %H:%M")
        
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
