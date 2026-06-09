import logging
from app.services.whatsapp_service import enviar_mensaje_whatsapp

logger = logging.getLogger(__name__)


def enviar_whatsapp_notificacion(numero_telefono: str, mensaje: str) -> bool:
    """
    Envía un mensaje de WhatsApp.
    numero_telefono debe tener formato internacional: +54911xxxxxxxx
    Retorna True si exitoso, False si falla.
    """
    return enviar_mensaje_whatsapp(numero_telefono, mensaje)


def formatear_cuota_vence(descripcion: str, monto: float, fecha_str: str, dias: int) -> str:
    return (
        f"📅 *Cuota próxima a vencer*\n\n"
        f"*{descripcion}*\n"
        f"Monto: ${monto:,.0f}\n"
        f"Vence: {fecha_str} (en {dias} {'día' if dias == 1 else 'días'})"
    )


def formatear_presupuesto_limite(categoria: str, porcentaje: int, usado: float, limite: float) -> str:
    return (
        f"⚠️ *Presupuesto al {porcentaje}%*\n\n"
        f"Categoría: *{categoria}*\n"
        f"Usado: ${usado:,.0f} de ${limite:,.0f}"
    )


def formatear_presupuesto_agotado(categoria: str, usado: float, limite: float) -> str:
    excedido = max(0, usado - limite)
    return (
        f"🔴 *Presupuesto agotado*\n\n"
        f"Categoría: *{categoria}*\n"
        f"Excediste el límite por ${excedido:,.0f}"
    )


def formatear_suscripcion_proxima(nombre: str, monto: float, dias: int) -> str:
    return (
        f"💳 *Suscripción próxima*\n\n"
        f"*{nombre}* se cobra en {dias} {'día' if dias == 1 else 'días'}\n"
        f"Monto: ${monto:,.0f}"
    )


def formatear_suscripcion_hoy(nombre: str, monto: float) -> str:
    return (
        f"💳 *Cobro de suscripción*\n\n"
        f"Hoy se cobra *{nombre}*\n"
        f"Monto: ${monto:,.0f}"
    )


def formatear_saldo_cero(billetera: str) -> str:
    return f"⚠️ *Saldo en cero*\n\nTu billetera *{billetera}* llegó a saldo cero."


def formatear_meta_alcanzada(nombre: str, monto: float) -> str:
    return f"🎯 *¡Meta alcanzada!*\n\nLlegaste a tu meta *{nombre}* de ${monto:,.0f}. ¡Bien hecho!"


def formatear_inactividad(dias: int) -> str:
    return f"👋 *¿Todo bien?*\n\nHace {dias} días que no registrás movimientos en Argentum."


def formatear_resumen_diario(mensajes: list[str]) -> str:
    items = "\n".join(f"• {m}" for m in mensajes)
    return f"🔔 *Resumen de hoy — Argentum*\n\n{items}\n\nAbrí Argentum para más detalles."
