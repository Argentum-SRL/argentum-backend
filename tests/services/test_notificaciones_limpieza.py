import uuid
from app.utils.texto import limpiar_mensaje_web
from app.models.notificacion import (
    MENSAJE_CAMBIO_CONTRASENA,
    MENSAJE_CAMBIO_CONTRASENA_WPP,
    TipoNotificacion,
    NivelNotificacion,
)
from app.schemas.notificacion import NotificacionRead, NotificacionBase


def test_limpiar_mensaje_web_cambio_password():
    # Mensaje histórico con URL
    mensaje_con_link = (
        "Tu contraseña de Argentum fue actualizada. Si no fuiste vos, "
        "cambiala de inmediato desde https://miargentum.com/auth/recuperar-password"
    )
    resultado = limpiar_mensaje_web(mensaje_con_link)
    assert resultado == "Tu contraseña de Argentum fue actualizada. Si no fuiste vos, cambiala de inmediato."
    assert "https://" not in resultado
    assert "recuperar-password" not in resultado


def test_limpiar_mensaje_web_variantes():
    # Mensaje sin link
    assert limpiar_mensaje_web(MENSAJE_CAMBIO_CONTRASENA) == MENSAJE_CAMBIO_CONTRASENA

    # Mensaje con link y coma
    msg2 = "Revisá tu resumen, ingresando a https://miargentum.com/app"
    assert limpiar_mensaje_web(msg2) == "Revisá tu resumen."

    # Mensaje con link http o www
    msg3 = "Ingresá en www.miargentum.com para más datos"
    assert "www.miargentum.com" not in limpiar_mensaje_web(msg3)

    # Mensaje con markdown link
    msg4 = "Visitá [nuestro sitio](https://miargentum.com) para ver más."
    assert limpiar_mensaje_web(msg4) == "Visitá nuestro sitio para ver más."


def test_notificacion_schema_strips_urls():
    raw_data = {
        "id": uuid.uuid4(),
        "usuario_id": uuid.uuid4(),
        "tipo": TipoNotificacion.CAMBIO_CONTRASENA,
        "nivel": NivelNotificacion.CRITICA,
        "mensaje": MENSAJE_CAMBIO_CONTRASENA_WPP,
        "leida": False,
        "canal_web": True,
        "canal_whatsapp": True,
        "canal_email": False,
        "enviada_whatsapp": False,
        "enviada_email": False,
        "created_at": "2026-10-08T10:00:00Z",
    }
    notif = NotificacionRead(**raw_data)
    assert notif.mensaje == "Tu contraseña de Argentum fue actualizada. Si no fuiste vos, cambiala de inmediato."
    assert "https://" not in notif.mensaje
