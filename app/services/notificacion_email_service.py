import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from app.core.config import settings

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


def _html_base(contenido_inner: str) -> str:
    return f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto; padding: 32px 24px; background: #ffffff;">
      <h2 style="font-size: 20px; font-weight: 600; color: #0D2045; margin: 0 0 24px;">Argentum</h2>
      {contenido_inner}
      <hr style="border: none; border-top: 1px solid #E5E7EB; margin: 32px 0 16px;" />
      <p style="font-size: 12px; color: #8A95A8; margin: 0;">
        Este es un mensaje automático de seguridad de Argentum. No lo reenvíes ni compartas su contenido.
      </p>
    </div>
    """


def generar_email_cambio_contrasena(usuario_nombre: str) -> tuple[str, str, str]:
    from datetime import datetime
    ts = (datetime.utcnow() - __import__('datetime').timedelta(hours=-3)).strftime("%d/%m/%Y a las %H:%M")
    asunto = "Argentum — Cambio de contraseña detectado"
    html = _html_base(f"""
      <p style="font-size: 16px; color: #0A0D12; margin: 0 0 12px;">Hola {usuario_nombre},</p>
      <p style="font-size: 14px; color: #0A0D12; margin: 0 0 12px;">
        Se cambió la contraseña de tu cuenta el <strong>{ts}</strong> (hora Argentina).
      </p>
      <p style="font-size: 14px; color: #A32D2D; font-weight: 600; margin: 0;">
        Si no fuiste vos, contactanos de inmediato respondiendo este email o cambiá tu contraseña de emergencia.
      </p>
    """)
    texto = (
        f"Hola {usuario_nombre},\n\n"
        f"Se cambió la contraseña de tu cuenta el {ts} (hora Argentina).\n\n"
        f"Si no fuiste vos, contactanos de inmediato."
    )
    return asunto, html, texto


def generar_email_nuevo_dispositivo(usuario_nombre: str, ip: str = None) -> tuple[str, str, str]:
    from datetime import datetime, timedelta
    ts = (datetime.utcnow() + timedelta(hours=-3)).strftime("%d/%m/%Y a las %H:%M")
    ip_info = f" desde la IP {ip}" if ip else ""
    asunto = "Argentum — Inicio de sesión desde un nuevo dispositivo"
    html = _html_base(f"""
      <p style="font-size: 16px; color: #0A0D12; margin: 0 0 12px;">Hola {usuario_nombre},</p>
      <p style="font-size: 14px; color: #0A0D12; margin: 0 0 12px;">
        Se detectó un inicio de sesión nuevo{ip_info} el <strong>{ts}</strong> (hora Argentina).
      </p>
      <p style="font-size: 14px; color: #A32D2D; font-weight: 600; margin: 0;">
        Si no fuiste vos, cambiá tu contraseña de inmediato.
      </p>
    """)
    texto = (
        f"Hola {usuario_nombre},\n\n"
        f"Nuevo inicio de sesión{ip_info} el {ts} (hora Argentina).\n\n"
        f"Si no fuiste vos, cambiá tu contraseña."
    )
    return asunto, html, texto


def generar_email_intentos_login(usuario_nombre: str) -> tuple[str, str, str]:
    asunto = "Argentum — Múltiples intentos de acceso fallidos"
    html = _html_base(f"""
      <p style="font-size: 16px; color: #0A0D12; margin: 0 0 12px;">Hola {usuario_nombre},</p>
      <p style="font-size: 14px; color: #0A0D12; margin: 0 0 12px;">
        Detectamos múltiples intentos fallidos de acceso a tu cuenta.
      </p>
      <p style="font-size: 14px; color: #A32D2D; font-weight: 600; margin: 0;">
        Si no fuiste vos, te recomendamos cambiar tu contraseña de inmediato.
      </p>
    """)
    texto = (
        f"Hola {usuario_nombre},\n\n"
        f"Detectamos múltiples intentos fallidos de acceso. Si no fuiste vos, cambiá tu contraseña."
    )
    return asunto, html, texto
