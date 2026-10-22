"""
Catálogo descriptivo de tareas automáticas (APScheduler) de Argentum.
Define para cada job su nombre en castellano simple, descripción de función,
período esperado en minutos y si admite ejecución manual por parte del admin.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JobCatalogoItem:
    job_id: str
    nombre: str
    que_hace: str
    periodo_esperado_minutos: int
    puede_correr_ahora: bool = False


# Conjunto de jobs seguros que un administrador puede forzar a mano
JOBS_MANUALES_PERMITIDOS: frozenset[str] = frozenset({
    "guardar_cotizaciones_diarias",
    "actualizar_tasas_diarias",
    "refresh_feriados_argentina",
    "actualizar_ipc_diario",
    "limpiar_refresh_tokens",
    "vigilar_deriva_saldos",
})


# Catálogo completo con las 21 tareas originales de main.py más el vigilante
JOBS_CATALOGO: list[JobCatalogoItem] = [
    JobCatalogoItem(
        job_id="limpiar_refresh_tokens",
        nombre="Limpieza de sesiones y tokens",
        que_hace="Elimina tokens vencidos, códigos de verificación, mensajes antiguos de WhatsApp y registros de costo de la IA de más de 400 días.",
        periodo_esperado_minutos=360,
        puede_correr_ahora=True,
    ),
    JobCatalogoItem(
        job_id="vencimientos_tarjetas",
        nombre="Vencimientos de tarjetas",
        que_hace="Genera los movimientos de vencimiento de tarjetas de crédito del día.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="renovar_presupuestos",
        nombre="Renovación de presupuestos",
        que_hace="Crea los nuevos períodos de presupuestos mensuales al comenzar el ciclo.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="cobros_suscripciones",
        nombre="Cobros de suscripciones",
        que_hace="Registra los gastos automáticos de suscripciones activas que vencen hoy.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="notificaciones_cuotas",
        nombre="Avisos de cuotas por vencer",
        que_hace="Notifica a los usuarios sobre cuotas de tarjetas próximas a vencer.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="notificaciones_presupuestos",
        nombre="Avisos de presupuestos",
        que_hace="Avisa cuando un usuario está cerca del límite o superó su presupuesto.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="notificaciones_suscripciones",
        nombre="Avisos de suscripciones",
        que_hace="Informa sobre próximos débitos de suscripciones y servicios.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="notificaciones_inactividad",
        nombre="Avisos de inactividad",
        que_hace="Envía un recordatorio a usuarios que llevan varios días sin registrar gastos.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="resumen_cierre_ciclo",
        nombre="Resumen de cierre de ciclo",
        que_hace="Envía el balance general de gastos e ingresos al terminar el ciclo mensual.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="proyeccion_negativa",
        nombre="Alerta de saldo futuro negativo",
        que_hace="Avisa al usuario si sus gastos previstos superan el dinero disponible.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="notificaciones_facturas",
        nombre="Avisos de facturas e impuestos",
        que_hace="Recuerda el vencimiento cercano de servicios públicos y facturas cargadas.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="resumen_semanal",
        nombre="Resumen semanal de gastos",
        que_hace="Envía los lunes un resumen de la actividad financiera de la semana anterior.",
        periodo_esperado_minutos=10080,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="entrega_whatsapp_batched",
        nombre="Envío de mensajes de WhatsApp",
        que_hace="Despacha en tandas los mensajes y notificaciones pendientes de WhatsApp.",
        periodo_esperado_minutos=1,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="reintentar_avisos_inmediatos",
        nombre="Reintento de avisos inmediatos",
        que_hace="Reintenta el envío de notificaciones urgentes que no pudieron entregarse.",
        periodo_esperado_minutos=2,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="actualizar_perfiles_financieros",
        nombre="Cálculo de perfiles financieros",
        que_hace="Actualiza los patrones de ahorro y salud financiera de los usuarios activos.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="refresh_feriados_argentina",
        nombre="Actualización de feriados",
        que_hace="Consulta y guarda el calendario oficial de feriados de Argentina.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=True,
    ),
    JobCatalogoItem(
        job_id="recalcular_calibraciones_nocturno",
        nombre="Calibración nocturna de proyecciones",
        que_hace="Ajusta la precisión de las proyecciones automáticas de saldo para cada usuario.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="actualizar_tasas_diarias",
        nombre="Actualización de tasas de interés",
        que_hace="Descarga las tasas de rendimiento de billeteras virtuales y bancos.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=True,
    ),
    JobCatalogoItem(
        job_id="aplicar_movimientos_con_fecha_llegada",
        nombre="Aplicación de movimientos programados",
        que_hace="Aplica en los saldos reales los cobros y pagos cuya fecha programada llegó.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="guardar_cotizaciones_diarias",
        nombre="Registro de cotizaciones del dólar",
        que_hace="Guarda el valor de los diferentes tipos de dólar oficial y financieros.",
        periodo_esperado_minutos=60,
        puede_correr_ahora=True,
    ),
    JobCatalogoItem(
        job_id="actualizar_ipc_diario",
        nombre="Actualización de inflación (IPC)",
        que_hace="Obtiene y almacena el índice de precios al consumidor oficial.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=True,
    ),
    JobCatalogoItem(
        job_id="vigilar_tareas",
        nombre="Vigilancia de tareas automáticas",
        que_hace="Revisa el estado de todas las tareas y alerta por email si alguna falla o se atrasa.",
        periodo_esperado_minutos=60,
        puede_correr_ahora=False,
    ),
    JobCatalogoItem(
        job_id="vigilar_deriva_saldos",
        nombre="Control de saldos",
        que_hace="Cada noche compara el saldo guardado de cada billetera con el que sale de sus movimientos y avisa por email cuántas no coinciden.",
        periodo_esperado_minutos=1440,
        puede_correr_ahora=True,
    ),
]

JOBS_POR_ID: dict[str, JobCatalogoItem] = {j.job_id: j for j in JOBS_CATALOGO}
