from app.core.config import settings


def _generar_html_base(
    color_header: str,
    subtitulo: str,
    badge_bg: str,
    badge_text: str,
    badge_label: str,
    titulo: str,
    contenido_html: str,
    nota_footer_body: str,
    texto_footer: str,
) -> str:
    """
    Función helper privada para envolver el contenido de los emails
    en la estructura HTML base aprobada.
    """
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Argentum</title>
</head>
<body style="margin: 0; padding: 0; background-color: #F5F4F0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #F5F4F0; padding: 32px 16px;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" border="0" style="max-width: 560px; width: 100%; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">

          <!-- HEADER -->
          <tr>
            <td style="background-color: {color_header}; padding: 24px 32px;">
              <table cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="padding-right: 12px; vertical-align: middle;">
                    <img src="https://res.cloudinary.com/dhma0b3rz/image/upload/v1781813348/argentum-app-icon_tws3qb.png" width="36" height="36" alt="Argentum" style="display: block; border-radius: 8px;">
                  </td>
                  <td style="vertical-align: middle;">
                    <div style="font-size: 18px; font-weight: 600; color: #ffffff; letter-spacing: -0.3px; line-height: 1.2;">Argentum</div>
                    <div style="font-size: 10px; color: rgba(255,255,255,0.5); letter-spacing: 1px; margin-top: 2px;">{subtitulo}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- BODY -->
          <tr>
            <td style="background-color: #ffffff; padding: 32px;">

              <!-- BADGE -->
              <div style="display: inline-block; background-color: {badge_bg}; color: {badge_text}; font-size: 11px; font-weight: 500; padding: 4px 12px; border-radius: 20px; margin-bottom: 16px;">{badge_label}</div>

              <!-- H1 -->
              <h1 style="font-size: 22px; font-weight: 600; color: #0A0D12; margin: 0 0 14px 0; line-height: 1.3; letter-spacing: -0.3px;">{titulo}</h1>

              <!-- CONTENIDO ESPECÍFICO -->
              {contenido_html}

              <!-- SEPARADOR -->
              <div style="height: 1px; background-color: #E8E8E4; margin: 24px 0;"></div>

              <!-- FOOTER NOTE (dentro del body) -->
              <p style="font-size: 12px; color: #8A95A8; margin: 0; line-height: 1.6;">{nota_footer_body}</p>

            </td>
          </tr>

          <!-- FOOTER -->
          <tr>
            <td style="background-color: {color_header}; padding: 20px 32px; text-align: center;">
              <table cellpadding="0" cellspacing="0" border="0" width="100%">
                <tr>
                  <td align="center" style="padding-bottom: 10px;">
                    <img src="https://res.cloudinary.com/dhma0b3rz/image/upload/v1781813348/argentum-app-icon_tws3qb.png" width="24" height="24" alt="" style="display: inline-block; border-radius: 6px; vertical-align: middle; margin-right: 6px;">
                    <span style="font-size: 13px; font-weight: 500; color: rgba(255,255,255,0.6); vertical-align: middle;">Argentum</span>
                  </td>
                </tr>
                <tr>
                  <td align="center">
                    <p style="font-size: 11px; color: rgba(255,255,255,0.4); margin: 0; line-height: 1.7;">{texto_footer}</p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def template_verificacion_email(nombre: str, codigo: str) -> str:
    """
    Template 1: Verificación de cuenta al registrarse o solicitar reenvío.
    """
    contenido = f"""
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 20px 0;">
      Hola {nombre}, usá el código a continuación para verificar tu cuenta en Argentum. Válido por 15 minutos.
    </p>

    <!-- BLOQUE DE CÓDIGO -->
    <div style="background-color: #F5F4F0; border: 1px solid #E8E8E4; border-radius: 8px; padding: 24px; text-align: center; margin-bottom: 24px;">
      <div style="font-size: 36px; font-weight: 700; letter-spacing: 12px; color: #0D2045; font-family: monospace; line-height: 1;">
        {codigo}
      </div>
      <div style="font-size: 12px; color: #8A95A8; margin-top: 8px;">
        No compartás este código
      </div>
    </div>

    <!-- SECURITY NOTE -->
    <div style="background-color: #FFF8F0; border-left: 3px solid #A8905A; border-radius: 0 6px 6px 0; padding: 12px 16px; font-size: 13px; color: #5F4A2A; line-height: 1.5;">
      Argentum nunca te va a pedir este código por WhatsApp, teléfono ni chat. Si alguien lo hace, ignoralo.
    </div>
    """
    return _generar_html_base(
        color_header="#0D2045",
        subtitulo="VERIFICACIÓN DE CUENTA",
        badge_bg="#E6F1FB",
        badge_text="#0C447C",
        badge_label="Verificación requerida",
        titulo="Verificá tu email para entrar a Argentum",
        contenido_html=contenido,
        nota_footer_body="Si no fuiste vos, podés ignorar este email. Tu cuenta está segura.",
        texto_footer="Recibís este correo porque tu cuenta solicitó un código de verificación.",
    )


def template_recupero_contrasena(nombre: str, link: str) -> str:
    """
    Template 2: Recuperación de acceso a la cuenta.
    """
    contenido = f"""
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 16px 0;">
      Hacé clic en el botón para crear una contraseña nueva.
    </p>
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 24px 0;">
      Si no fuiste vos, podés ignorar este email. Tu cuenta está segura.
    </p>

    <!-- CTA BUTTON -->
    <div style="text-align: center; margin-bottom: 12px;">
      <a href="{link}" style="display: inline-block; background-color: #0D2045; color: #ffffff; font-size: 14px; font-weight: 600; text-decoration: none; padding: 13px 28px; border-radius: 8px;">
        Cambiar mi contraseña
      </a>
    </div>
    <div style="text-align: center; font-size: 13px; color: #8A95A8; margin-bottom: 24px;">
      El enlace expira en 30 minutos.
    </div>

    <!-- SECURITY NOTE -->
    <div style="background-color: #FFF8F0; border-left: 3px solid #A8905A; border-radius: 0 6px 6px 0; padding: 12px 16px; font-size: 13px; color: #5F4A2A; line-height: 1.5;">
      Si no fuiste vos, podés ignorar este email. Tu cuenta está segura.
    </div>
    """
    return _generar_html_base(
        color_header="#0D2045",
        subtitulo="RECUPERO DE CONTRASEÑA",
        badge_bg="#E6F1FB",
        badge_text="#0C447C",
        badge_label="Acción solicitada",
        titulo="Cambiá tu contraseña de Argentum",
        contenido_html=contenido,
        nota_footer_body="Si no fuiste vos, podés ignorar este email. Tu cuenta está segura.",
        texto_footer="Este es un mensaje automático de seguridad de Argentum.",
    )


def template_cambio_contrasena(nombre: str, fecha_hora_argentina: str, dispositivo: str) -> str:
    """
    Template 3: Confirmación de contraseña cambiada.
    """
    contenido = f"""
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 16px 0;">
      Hola {nombre}, te confirmamos que la contraseña de tu cuenta de Argentum fue modificada exitosamente.
    </p>

    <!-- INFO BOX -->
    <div style="background-color: #F5F4F0; border-radius: 8px; padding: 16px 20px; font-size: 14px; color: #3a3d42; margin-bottom: 16px; line-height: 1.6;">
      <div style="margin-bottom: 8px;"><strong style="color: #0A0D12;">Cuándo:</strong> {fecha_hora_argentina}</div>
      <div><strong style="color: #0A0D12;">Dispositivo:</strong> {dispositivo}</div>
    </div>

    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 20px 0;">
      Si fuiste vos, no hace falta que hagas nada más.
    </p>

    <!-- DANGER BOX -->
    <div style="background-color: #FCEBEB; border-left: 3px solid #A32D2D; border-radius: 0 6px 6px 0; padding: 12px 16px; font-size: 13px; color: #791F1F; line-height: 1.5;">
      Si no realizaste este cambio, contactanos de inmediato escribiendo a <a href="mailto:srlargentum@gmail.com" style="color: #791F1F; font-weight: bold; text-decoration: underline;">srlargentum@gmail.com</a>
    </div>
    """
    return _generar_html_base(
        color_header="#0D2045",
        subtitulo="SEGURIDAD DE CUENTA",
        badge_bg="#EAF3DE",
        badge_text="#27500A",
        badge_label="Contraseña actualizada",
        titulo="Tu contraseña fue cambiada.",
        contenido_html=contenido,
        nota_footer_body="Este es un correo de seguridad automático de Argentum.",
        texto_footer="Este correo es informativo de seguridad y no puede desactivarse.",
    )


def template_nuevo_dispositivo(nombre: str, fecha_hora_argentina: str, dispositivo: str, ubicacion: str, link_bloqueo: str) -> str:
    """
    Template 4: Alerta de acceso desde nuevo dispositivo.
    Nota: Utiliza fondo rojo en header y footer.
    """
    contenido = f"""
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 16px 0;">
      Hola {nombre}, detectamos un inicio de sesión en tu cuenta desde un dispositivo o ubicación que no reconocemos.
    </p>

    <!-- DANGER INFO BOX -->
    <div style="background-color: #FCEBEB; border-left: 3px solid #A32D2D; border-radius: 0 6px 6px 0; padding: 16px 20px; font-size: 14px; color: #3a3d42; margin-bottom: 20px; line-height: 1.6;">
      <div style="margin-bottom: 8px;"><strong style="color: #791F1F;">Cuándo:</strong> {fecha_hora_argentina}</div>
      <div style="margin-bottom: 8px;"><strong style="color: #791F1F;">Dispositivo:</strong> {dispositivo}</div>
      <div><strong style="color: #791F1F;">Ubicación:</strong> {ubicacion}</div>
    </div>

    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 24px 0;">
      ¿Fuiste vos? No hace falta que hagas nada. Si no reconocés este acceso, bloqueá tu cuenta de inmediato.
    </p>

    <!-- CTA BUTTON ROJO -->
    <div style="text-align: center; margin-bottom: 12px;">
      <a href="{link_bloqueo}" style="display: inline-block; background-color: #A32D2D; color: #ffffff; font-size: 14px; font-weight: 600; text-decoration: none; padding: 13px 28px; border-radius: 8px;">
        Bloquear mi cuenta
      </a>
    </div>
    <div style="text-align: center; font-size: 13px; color: #8A95A8; margin-bottom: 24px;">
      O escribinos a <a href="mailto:srlargentum@gmail.com" style="color: #8A95A8; text-decoration: underline;">srlargentum@gmail.com</a> y te ayudamos.
    </div>
    """
    return _generar_html_base(
        color_header="#791F1F",
        subtitulo="ALERTA DE SEGURIDAD",
        badge_bg="#FCEBEB",
        badge_text="#791F1F",
        badge_label="Acción requerida",
        titulo="Acceso desde un nuevo dispositivo.",
        contenido_html=contenido,
        nota_footer_body="Esta alerta no puede desactivarse. Es parte de la seguridad de tu cuenta.",
        texto_footer="Este es un correo de seguridad crítico. No puede desactivarse.",
    )


def template_intentos_login(nombre: str, cantidad_intentos: int, fecha_hora_argentina: str, link_recupero: str) -> str:
    """
    Template 5: Alerta de múltiples intentos de login fallidos.
    Nota: Utiliza fondo rojo en header y footer.
    """
    contenido = f"""
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 16px 0;">
      Hola {nombre}, registramos múltiples intentos de inicio de sesión fallidos en tu cuenta de Argentum.
    </p>

    <!-- INFO BOX -->
    <div style="background-color: #FCEBEB; border-left: 3px solid #A32D2D; border-radius: 0 6px 6px 0; padding: 16px 20px; font-size: 14px; color: #3a3d42; margin-bottom: 20px; line-height: 1.6;">
      <div style="margin-bottom: 8px;"><strong style="color: #791F1F;">Cantidad de intentos:</strong> {cantidad_intentos}</div>
      <div><strong style="color: #791F1F;">Último intento:</strong> {fecha_hora_argentina}</div>
    </div>

    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 24px 0;">
      Si fuiste vos intentando ingresar y no recordás tu contraseña, podés restablecerla con el botón de abajo.
    </p>

    <!-- CTA BUTTON NAVY -->
    <div style="text-align: center; margin-bottom: 24px;">
      <a href="{link_recupero}" style="display: inline-block; background-color: #0D2045; color: #ffffff; font-size: 14px; font-weight: 600; text-decoration: none; padding: 13px 28px; border-radius: 8px;">
        Cambiar mi contraseña
      </a>
    </div>

    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0;">
      Si no fuiste vos, tu cuenta sigue protegida pero te recomendamos cambiar tu contraseña como precaución.
    </p>
    """
    return _generar_html_base(
        color_header="#791F1F",
        subtitulo="ALERTA DE SEGURIDAD",
        badge_bg="#FCEBEB",
        badge_text="#791F1F",
        badge_label="Actividad inusual",
        titulo=f"Detectamos {cantidad_intentos} intentos de acceso fallidos.",
        contenido_html=contenido,
        nota_footer_body="Este es un correo de seguridad automático. No puede desactivarse.",
        texto_footer="Este correo es automático e informativo.",
    )


def template_reset_password_email(nombre: str, reset_url: str) -> str:
    """
    Template 6: Restablecimiento de contraseña por parte de un administrador.
    """
    contenido = f"""
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 20px 0;">
      Hola {nombre}, un administrador solicitó el restablecimiento de tu contraseña en Argentum.
    </p>

    <!-- CTA BUTTON -->
    <div style="text-align: center; margin-bottom: 12px;">
      <a href="{reset_url}" style="display: inline-block; background-color: #0D2045; color: #ffffff; font-size: 15px; font-weight: 600; text-decoration: none; padding: 14px 28px; border-radius: 8px;">
        Cambiar mi contraseña
      </a>
    </div>

    <!-- LINK ALTERNATIVO -->
    <p style="font-size: 13px; color: #8A95A8; margin: 0 0 10px 0;">
      Si el botón no funciona, copiá y pegá este enlace en tu navegador:
    </p>
    <p style="font-size: 13px; margin: 0 0 24px 0; word-break: break-all;">
      <a href="{reset_url}" style="color: #0C447C; text-decoration: underline;">{reset_url}</a>
    </p>

    <!-- SECURITY NOTE -->
    <div style="background-color: #FFF8F0; border-left: 3px solid #A8905A; border-radius: 0 6px 6px 0; padding: 12px 16px; font-size: 13px; color: #5F4A2A; line-height: 1.5;">
      Este link es válido por 1 hora. Si no fuiste vos, podés ignorar este email. Tu cuenta está segura.
    </div>
    """
    return _generar_html_base(
        color_header="#0D2045",
        subtitulo="Alguien pidió cambiar tu contraseña de Argentum",
        badge_bg="#FFF3E0",
        badge_text="#A8905A",
        badge_label="Acción requerida",
        titulo="Cambiá tu contraseña de Argentum",
        contenido_html=contenido,
        nota_footer_body="Si no fuiste vos, podés ignorar este email. Tu cuenta está segura.",
        texto_footer="Recibís este correo porque se solicitó un restablecimiento de contraseña para tu cuenta.",
    )


def template_bienvenida(nombre: str) -> str:
    """
    Template: Bienvenida tras completar la configuración de la cuenta (onboarding).
    """
    dashboard_url = f"{settings.FRONTEND_URL}/app/dashboard"
    contenido = f"""
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 16px 0;">
      Hola {nombre}, ya completaste la configuración inicial de tu cuenta en Argentum.
    </p>
    <p style="font-size: 14px; color: #3a3d42; line-height: 1.6; margin: 0 0 24px 0;">
      A partir de ahora podés empezar a registrar tus gastos e ingresos fácilmente enviando mensajes por WhatsApp o directamente desde la aplicación web.
    </p>

    <!-- CTA BUTTON -->
    <div style="text-align: center; margin-bottom: 24px;">
      <a href="{dashboard_url}" style="display: inline-block; background-color: #0D2045; color: #ffffff; font-size: 14px; font-weight: 600; text-decoration: none; padding: 13px 28px; border-radius: 8px;">
        Ir a mi dashboard
      </a>
    </div>

    <!-- INFO BOX -->
    <div style="background-color: #F5F4F0; border-radius: 8px; padding: 16px 20px; font-size: 13px; color: #3a3d42; line-height: 1.5;">
      Podés consultar tus saldos, billeteras y presupuestos en cualquier momento desde tu panel de control.
    </div>
    """
    return _generar_html_base(
        color_header="#0D2045",
        subtitulo="BIENVENIDO A ARGENTUM",
        badge_bg="#EAF3DE",
        badge_text="#27500A",
        badge_label="Cuenta lista",
        titulo=f"Tu cuenta ya está lista, {nombre}",
        contenido_html=contenido,
        nota_footer_body="Gracias por sumarte a Argentum.",
        texto_footer="Recibís este correo porque completaste la configuración de tu cuenta en Argentum.",
    )


