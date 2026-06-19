import logging
from app.services.whatsapp_service import enviar_mensaje_whatsapp

logger = logging.getLogger(__name__)


def _fmt(monto: float) -> str:
    """Formatea un número con formato argentino: punto para miles, sin decimales."""
    return f"${monto:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def enviar_whatsapp_notificacion(numero_telefono: str, mensaje: str) -> bool:
    """
    Envía un mensaje de WhatsApp.
    numero_telefono debe tener formato internacional: +54911xxxxxxxx
    Retorna True si exitoso, False si falla.
    """
    return enviar_mensaje_whatsapp(numero_telefono, mensaje)


def formatear_cuota_vence(descripcion: str, monto: float, fecha_str: str, dias: int) -> str:
    return f"Tu cuota de *{descripcion}* vence el *{fecha_str}*. Acordate de tener saldo disponible."

def formatear_presupuesto_limite(categoria: str, porcentaje: int, usado: float, limite: float) -> str:
    return (
        f"*Presupuesto al {porcentaje}%*\n\n"
        f"{categoria}: usaste {_fmt(usado)} de {_fmt(limite)}.\n"
        f"Te queda {_fmt(limite - usado)}."
    )

def formatear_presupuesto_agotado(categoria: str, usado: float, limite: float) -> str:
    return f"Superaste tu presupuesto de *{categoria}*. Llevás {_fmt(usado)} de {_fmt(limite)}."

def formatear_suscripcion_proxima(nombre: str, monto: float, dias: int) -> str:
    dias_txt = "mañana" if dias == 1 else f"en {dias} días"
    return (
        f"*{nombre}* se cobra {dias_txt}\n"
        f"{_fmt(monto)}"
    )

def formatear_suscripcion_hoy(nombre: str, monto: float) -> str:
    return (
        f"Hoy se cobra *{nombre}*\n"
        f"{_fmt(monto)}"
    )

def formatear_saldo_cero(billetera: str, monto: float = 0.0) -> str:
    return f"Tu billetera *{billetera}* tiene solo {_fmt(monto)} disponible."

def formatear_meta_alcanzada(nombre: str, monto: float) -> str:
    return f"Llegaste a tu meta *{nombre}* de {_fmt(monto)}."

def formatear_inactividad(dias: int) -> str:
    return f"Hace {dias} días que no registrás movimientos. ¿Todo bien?"

def formatear_resumen_diario(mensajes: list[str]) -> str:
    items = "\n".join(f"• {m}" for m in mensajes)
    return f"*Argentum — resumen de hoy*\n\n{items}"

def formatear_resumen_ciclo(
    total_ingresos: float,
    total_egresos: float,
    balance: float,
    categoria_top: str | None,
    monto_categoria_top: float | None,
    gastos_hormiga: list[dict] | None,
) -> str:
    signo = "+" if balance >= 0 else ""
    lineas = [
        f"*Cerraste el ciclo*\n",
        f"Ingresos: {_fmt(total_ingresos)}",
        f"Egresos: {_fmt(total_egresos)}",
        f"Balance: {signo}{_fmt(balance)}",
    ]
    if categoria_top and monto_categoria_top:
        lineas.append(f"\nMás gastaste en *{categoria_top}*: {_fmt(monto_categoria_top)}")
    if gastos_hormiga:
        for g in gastos_hormiga[:2]:
            lineas.append(f"• {g['categoria']}: {_fmt(g['total'])} en {g['cantidad']} compras")
    return "\n".join(lineas)
