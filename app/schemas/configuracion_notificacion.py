from __future__ import annotations

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class ConfiguracionNotificacionBase(BaseModel):
    usuario_id: UUID | None = None
    cuota_vence_anticipacion_dias: int = 3
    cuota_vence_web: bool = True
    cuota_vence_whatsapp: bool = True

    presupuesto_umbral_1: int = 80
    presupuesto_umbral_1_activo: bool = True
    presupuesto_umbral_1_web: bool = True
    presupuesto_umbral_1_whatsapp: bool = False

    presupuesto_umbral_2_web: bool = True
    presupuesto_umbral_2_whatsapp: bool = True

    suscripcion_hoy_web: bool = True
    suscripcion_hoy_whatsapp: bool = True

    suscripcion_recordatorio_activo: bool = True
    suscripcion_recordatorio_dias: int = 3
    suscripcion_recordatorio_web: bool = True
    suscripcion_recordatorio_whatsapp: bool = False

    meta_alcanzada_activo: bool = True
    meta_alcanzada_web: bool = True
    meta_alcanzada_whatsapp: bool = True

    saldo_cero_activo: bool = True
    saldo_cero_web: bool = True
    saldo_cero_whatsapp: bool = True

    gasto_inusual_activo: bool = True
    gasto_inusual_web: bool = True
    gasto_inusual_whatsapp: bool = False

    resumen_semanal_activo: bool = False
    resumen_semanal_web: bool = True
    resumen_semanal_whatsapp: bool = False

    inactividad_activo: bool = False
    inactividad_dias: int = 7
    inactividad_web: bool = True
    inactividad_whatsapp: bool = False

    resumen_ciclo_activo: bool = True
    resumen_ciclo_web: bool = False
    resumen_ciclo_whatsapp: bool = True

    proyeccion_negativa_activo: bool = True
    proyeccion_negativa_web: bool = True
    proyeccion_negativa_whatsapp: bool = True

    whatsapp_hora_envio: int = 9
    whatsapp_minuto_envio: int = 0


class ConfiguracionNotificacionCreate(ConfiguracionNotificacionBase):
    pass


class ConfiguracionNotificacionUpdate(BaseModel):
    cuota_vence_anticipacion_dias: int | None = None
    cuota_vence_web: bool | None = None
    cuota_vence_whatsapp: bool | None = None

    presupuesto_umbral_1: int | None = None
    presupuesto_umbral_1_activo: bool | None = None
    presupuesto_umbral_1_web: bool | None = None
    presupuesto_umbral_1_whatsapp: bool | None = None

    presupuesto_umbral_2_web: bool | None = None
    presupuesto_umbral_2_whatsapp: bool | None = None

    suscripcion_hoy_web: bool | None = None
    suscripcion_hoy_whatsapp: bool | None = None

    suscripcion_recordatorio_activo: bool | None = None
    suscripcion_recordatorio_dias: int | None = None
    suscripcion_recordatorio_web: bool | None = None
    suscripcion_recordatorio_whatsapp: bool | None = None

    meta_alcanzada_activo: bool | None = None
    meta_alcanzada_web: bool | None = None
    meta_alcanzada_whatsapp: bool | None = None

    saldo_cero_activo: bool | None = None
    saldo_cero_web: bool | None = None
    saldo_cero_whatsapp: bool | None = None

    gasto_inusual_activo: bool | None = None
    gasto_inusual_web: bool | None = None
    gasto_inusual_whatsapp: bool | None = None

    resumen_semanal_activo: bool | None = None
    resumen_semanal_web: bool | None = None
    resumen_semanal_whatsapp: bool | None = None

    inactividad_activo: bool | None = None
    inactividad_dias: int | None = None
    inactividad_web: bool | None = None
    inactividad_whatsapp: bool | None = None

    resumen_ciclo_activo: bool | None = None
    resumen_ciclo_web: bool | None = None
    resumen_ciclo_whatsapp: bool | None = None

    proyeccion_negativa_activo: bool | None = None
    proyeccion_negativa_web: bool | None = None
    proyeccion_negativa_whatsapp: bool | None = None

    whatsapp_hora_envio: int | None = None
    whatsapp_minuto_envio: int | None = None


class ConfiguracionNotificacionRead(ConfiguracionNotificacionBase):
    id: UUID
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)