from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.tipos import DecimalJSON


class PerfilFinancieroBase(BaseModel):
    usuario_id: UUID | None = None
    tasa_ahorro_ars: DecimalJSON | None = None
    tasa_ahorro_usd: DecimalJSON | None = None
    score_impulsividad_ars: int | None = None
    score_impulsividad_usd: int | None = None
    ratio_cuotas_ars: DecimalJSON | None = None
    ratio_cuotas_usd: DecimalJSON | None = None
    cumplimiento_presupuesto: DecimalJSON | None = None
    consistencia_registro: DecimalJSON | None = None
    porcentaje_suscripciones_ars: DecimalJSON | None = None
    porcentaje_suscripciones_usd: DecimalJSON | None = None
    ultima_actualizacion: datetime | None = None


class PerfilFinancieroCreate(PerfilFinancieroBase):
    pass


class PerfilFinancieroUpdate(BaseModel):
    tasa_ahorro_ars: DecimalJSON | None = None
    tasa_ahorro_usd: DecimalJSON | None = None
    score_impulsividad_ars: int | None = None
    score_impulsividad_usd: int | None = None
    ratio_cuotas_ars: DecimalJSON | None = None
    ratio_cuotas_usd: DecimalJSON | None = None
    cumplimiento_presupuesto: DecimalJSON | None = None
    consistencia_registro: DecimalJSON | None = None
    porcentaje_suscripciones_ars: DecimalJSON | None = None
    porcentaje_suscripciones_usd: DecimalJSON | None = None
    ultima_actualizacion: datetime | None = None


class PerfilFinancieroRead(PerfilFinancieroBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)


class PerfilNuevoRead(BaseModel):
    datos_suficientes: bool = True
    mensaje_insuficiente: str | None = None
    calidad_registro_advertencia: str | None = None
    ciclos_con_datos: int
    ciclos_observados: int
    nivel_confianza: str
    cobertura_registro: DecimalJSON
    ingreso_tipico_ars: DecimalJSON | None = None
    estabilidad_ingreso_mad: DecimalJSON | None = None
    ingreso_actual_percentil: DecimalJSON | None = None
    gasto_comprometido_ars: DecimalJSON
    gasto_comprometido_ratio: DecimalJSON | None = None
    gasto_habitos_ars: DecimalJSON | None = None
    gasto_habitos_ratio: DecimalJSON | None = None
    capacidad_ahorro: DecimalJSON | None = None
    capacidad_ahorro_min: DecimalJSON | None = None
    capacidad_ahorro_max: DecimalJSON | None = None
    capacidad_ahorro_percentil: DecimalJSON | None = None
    gasto_tipico_ars: DecimalJSON | None = None
    saldo_disponible_ars: DecimalJSON | None = None
    runway_meses: DecimalJSON | None = None
    volatilidad_gasto_variable: DecimalJSON | None = None
    gasto_actual_percentil: DecimalJSON | None = None
    consistencia_registro: DecimalJSON | None = None
    interpretaciones_relativas: dict[str, str] = {}
    metodo: str


class HistorialPerfilFinancieroRead(BaseModel):
    id: UUID
    usuario_id: UUID
    periodo_inicio: date
    periodo_fin: date
    tasa_ahorro_ars: DecimalJSON | None = None
    tasa_ahorro_usd: DecimalJSON | None = None
    score_impulsividad_ars: int | None = None
    score_impulsividad_usd: int | None = None
    ratio_cuotas_ars: DecimalJSON | None = None
    ratio_cuotas_usd: DecimalJSON | None = None
    cumplimiento_presupuesto: DecimalJSON | None = None
    consistencia_registro: DecimalJSON | None = None
    porcentaje_suscripciones_ars: DecimalJSON | None = None
    porcentaje_suscripciones_usd: DecimalJSON | None = None
    fecha_snapshot: datetime

    model_config = ConfigDict(from_attributes=True)
