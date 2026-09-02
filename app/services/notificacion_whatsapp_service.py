import logging
from typing import Union
from app.services.whatsapp_service import enviar_mensaje_whatsapp
from app.utils.formato import formatear_monto
from app.models.usuario import Moneda

logger = logging.getLogger(__name__)


def enviar_whatsapp_notificacion(numero_telefono: str, mensaje: str) -> bool:
    """
    Envía un mensaje de WhatsApp.
    numero_telefono debe tener formato internacional: +54911xxxxxxxx
    Retorna True si exitoso, False si falla.
    """
    return enviar_mensaje_whatsapp(numero_telefono, mensaje)


def formatear_cuota_vence(descripcion: str, monto: float, fecha_str: str, dias: int, moneda: Union[Moneda, str] = Moneda.ARS) -> str:
    return f"Tu cuota de *{descripcion}* vence el *{fecha_str}*. Acordate de tener saldo disponible."

def formatear_presupuesto_limite(categoria: str, porcentaje: int, usado: float, limite: float, moneda: Union[Moneda, str] = Moneda.ARS) -> str:
    return (
        f"*Presupuesto al {porcentaje}%*\n\n"
        f"{categoria}: usaste {formatear_monto(usado, moneda)} de {formatear_monto(limite, moneda)}.\n"
        f"Te queda {formatear_monto(limite - usado, moneda)}."
    )

def formatear_presupuesto_agotado(categoria: str, usado: float, limite: float, moneda: Union[Moneda, str] = Moneda.ARS) -> str:
    return f"Superaste tu presupuesto de *{categoria}*. Llevás {formatear_monto(usado, moneda)} de {formatear_monto(limite, moneda)}."

def formatear_suscripcion_proxima(nombre: str, monto: float, dias: int, moneda: Union[Moneda, str] = Moneda.ARS) -> str:
    dias_txt = "mañana" if dias == 1 else f"en {dias} días"
    return (
        f"*{nombre}* se cobra {dias_txt}\n"
        f"{formatear_monto(monto, moneda)}"
    )

def formatear_suscripcion_hoy(nombre: str, monto: float, moneda: Union[Moneda, str] = Moneda.ARS) -> str:
    return (
        f"Hoy se cobra *{nombre}*\n"
        f"{formatear_monto(monto, moneda)}"
    )

def formatear_saldo_cero(billetera: str, monto: float = 0.0, moneda: Union[Moneda, str] = Moneda.ARS) -> str:
    return f"Tu billetera *{billetera}* tiene solo {formatear_monto(monto, moneda)} disponible."

def formatear_meta_alcanzada(nombre: str, monto: float, moneda: Union[Moneda, str] = Moneda.ARS) -> str:
    return f"Llegaste a tu meta *{nombre}* de {formatear_monto(monto, moneda)}."

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
    moneda: Union[Moneda, str] = Moneda.ARS,
) -> str:
    signo = "+" if balance >= 0 else ""
    lineas = [
        f"*Cerraste el ciclo*\n",
        f"Ingresos: {formatear_monto(total_ingresos, moneda)}",
        f"Egresos: {formatear_monto(total_egresos, moneda)}",
        f"Balance: {signo}{formatear_monto(balance, moneda)}",
    ]
    if categoria_top and monto_categoria_top:
        lineas.append(f"\nMás gastaste en *{categoria_top}*: {formatear_monto(monto_categoria_top, moneda)}")
    if gastos_hormiga:
        for g in gastos_hormiga[:2]:
            lineas.append(f"• {g['categoria']}: {formatear_monto(g['total'], moneda)} en {g['cantidad']} compras")
    return "\n".join(lineas)
