"""
Política de notificaciones y despacho por canal de mensajería (WhatsApp).
Define qué notificaciones son inmediatas o programadas, las plantillas permitidas
y las funciones de validación para entrega.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.core.config import settings
from app.models.notificacion import TipoNotificacion

# Clasificación de tipos de notificación para el canal WhatsApp
POLITICA_WHATSAPP: dict[TipoNotificacion, str] = {
    TipoNotificacion.CAMBIO_CONTRASENA: "inmediata",
    TipoNotificacion.CAMBIO_EMAIL: "inmediata",
    TipoNotificacion.RESUMEN_CICLO: "programada",
    TipoNotificacion.RESUMEN_SEMANAL: "programada",
    TipoNotificacion.INACTIVIDAD: "programada",
    TipoNotificacion.SUSCRIPCION_HOY: "programada",
    TipoNotificacion.SUSCRIPCION_PROXIMA: "programada",
    TipoNotificacion.CUOTA_VENCE: "programada",
    TipoNotificacion.PRESUPUESTO_LIMITE: "programada",
    TipoNotificacion.PRESUPUESTO_AGOTADO: "programada",
    TipoNotificacion.SALDO_CERO: "programada",
}

# Constantes de antigüedad y ventanas de despacho
MAX_EDAD_PROGRAMADA = timedelta(hours=72)
VENTANA_REINTENTO_INMEDIATA_MAX = timedelta(minutes=15)
VENTANA_REINTENTO_INMEDIATA_MIN = timedelta(seconds=90)
VENTANA_ALERTA_ADMIN_MAX = timedelta(minutes=17)
VENTANA_ALERTA_ADMIN_MIN = timedelta(minutes=15)


def plantilla_y_valores(notif: Any) -> tuple[str, list[str]] | None:
    """
    Determina el nombre de la plantilla de WhatsApp y los parámetros de reemplazo
    a partir de la notificación y sus datos estructurados.
    """
    dt = getattr(notif, "datos_template", None)
    template_name = None
    valores = None

    if dt is not None and isinstance(dt, dict):
        tipo = notif.tipo
        if tipo == TipoNotificacion.SALDO_CERO and "billetera_nombre" in dt:
            template_name = "alerta_saldo_cero"
            valores = [dt.get("billetera_nombre")]
        elif tipo == TipoNotificacion.PRESUPUESTO_LIMITE and all(k in dt for k in ("gastado_fmt", "limite_fmt", "nombre_pres")):
            template_name = "alerta_presupuesto_limite"
            valores = [dt.get("gastado_fmt"), dt.get("limite_fmt"), dt.get("nombre_pres")]
        elif tipo == TipoNotificacion.PRESUPUESTO_AGOTADO and all(k in dt for k in ("nombre_pres", "gastado_fmt", "limite_fmt")):
            template_name = "alerta_presupuesto_agotado"
            valores = [dt.get("nombre_pres"), dt.get("gastado_fmt"), dt.get("limite_fmt")]
        elif tipo == TipoNotificacion.CUOTA_VENCE:
            if "cuota_progreso" in dt and all(k in dt for k in ("cuota_progreso", "descripcion", "fecha", "monto_fmt")):
                template_name = "alerta_cuota_vence"
                valores = [dt.get("cuota_progreso"), dt.get("descripcion"), dt.get("fecha"), dt.get("monto_fmt")]
            elif "tarjeta_nombre" in dt and all(k in dt for k in ("tarjeta_nombre", "fecha_cierre", "fecha_vencimiento")):
                template_name = "alerta_resumen_tarjeta"
                valores = [dt.get("tarjeta_nombre"), dt.get("fecha_cierre"), dt.get("fecha_vencimiento")]
        elif tipo in (TipoNotificacion.SUSCRIPCION_HOY, TipoNotificacion.SUSCRIPCION_PROXIMA) and all(k in dt for k in ("nombre", "cuando", "monto_fmt")):
            template_name = "alerta_suscripcion_cobro"
            valores = [dt.get("nombre"), dt.get("cuando"), dt.get("monto_fmt")]
        elif tipo == TipoNotificacion.INACTIVIDAD and "dias" in dt:
            template_name = "alerta_inactividad"
            valores = [str(dt.get("dias"))]
        elif tipo == TipoNotificacion.RESUMEN_SEMANAL and all(k in dt for k in ("ingresos", "egresos", "balance", "top_categoria")):
            template_name = "resumen_semanal"
            valores = [dt.get("ingresos"), dt.get("egresos"), dt.get("balance"), dt.get("top_categoria")]
        elif tipo == TipoNotificacion.RESUMEN_CICLO and all(k in dt for k in ("ingresos", "egresos", "balance", "top_categoria")):
            template_name = "resumen_ciclo"
            valores = [dt.get("ingresos"), dt.get("egresos"), dt.get("balance"), dt.get("top_categoria")]
        elif tipo == TipoNotificacion.CAMBIO_CONTRASENA:
            template_name = "alerta_cambio_contrasena"
            valores = []
        elif tipo == TipoNotificacion.CAMBIO_EMAIL and "email" in dt:
            template_name = "alerta_cambio_email"
            valores = [dt.get("email")]
    elif getattr(notif, "tipo", None) == TipoNotificacion.CAMBIO_CONTRASENA:
        template_name = "alerta_cambio_contrasena"
        valores = []

    if template_name is not None and valores is not None:
        return template_name, [str(v) for v in valores]
    return None


def plantillas_activas() -> set[str]:
    """
    Retorna el conjunto de nombres de plantillas habilitadas según la variable
    de configuración WHATSAPP_PLANTILLAS_ACTIVAS.
    """
    raw = getattr(settings, "WHATSAPP_PLANTILLAS_ACTIVAS", "") or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


def puede_salir_por_whatsapp(notif: Any) -> bool:
    """
    Indica si una notificación califica para ser enviada por WhatsApp:
    el tipo debe estar en POLITICA_WHATSAPP, debe poseer plantilla/valores válidos,
    y la plantilla resultante debe estar activa en la configuración.
    """
    if getattr(notif, "tipo", None) not in POLITICA_WHATSAPP:
        return False
    pv = plantilla_y_valores(notif)
    if pv is None:
        return False
    template_name, _ = pv
    return template_name in plantillas_activas()


def componentes(valores: list[str] | list[Any] | None) -> list[dict[str, Any]]:
    """
    Construye la lista de componentes de parámetros para la plantilla de Meta Graph API.
    """
    if not valores:
        return []
    return [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": str(v)} for v in valores],
        }
    ]


def calcular_whatsapp_tipos_activos() -> list[str]:
    """
    Calcula la lista ordenada de prefijos de configuración activos en la web
    a partir de las plantillas habilitadas en plantillas_activas().
    """
    activas = plantillas_activas()
    res: list[str] = []
    if "alerta_cuota_vence" in activas or "alerta_resumen_tarjeta" in activas:
        res.append("cuota_vence")
    if "alerta_presupuesto_limite" in activas:
        res.append("presupuesto_umbral_1")
    if "alerta_presupuesto_agotado" in activas:
        res.append("presupuesto_umbral_2")
    if "alerta_suscripcion_cobro" in activas:
        res.append("suscripcion_hoy")
        res.append("suscripcion_recordatorio")
    if "alerta_saldo_cero" in activas:
        res.append("saldo_cero")
    if "resumen_semanal" in activas:
        res.append("resumen_semanal")
    if "alerta_inactividad" in activas:
        res.append("inactividad")
    if "resumen_ciclo" in activas:
        res.append("resumen_ciclo")
    return sorted(res)
