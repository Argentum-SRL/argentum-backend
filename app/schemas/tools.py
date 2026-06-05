from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, field_validator


class InstallmentConvenienceRequest(BaseModel):
    precio_contado: float
    precio_total_cuotas: float
    cantidad_cuotas: int
    inflacion_mensual: float

    @field_validator('precio_contado')
    @classmethod
    def precio_contado_positivo(cls, v):
        if v <= 0:
            raise ValueError('El precio de contado debe ser mayor a 0')
        return v

    @field_validator('precio_total_cuotas')
    @classmethod
    def precio_cuotas_positivo(cls, v):
        if v <= 0:
            raise ValueError('El precio en cuotas debe ser mayor a 0')
        return v

    @field_validator('cantidad_cuotas')
    @classmethod
    def cuotas_validas(cls, v):
        if v < 1 or v > 60:
            raise ValueError('La cantidad de cuotas debe estar entre 1 y 60')
        return v

    @field_validator('inflacion_mensual')
    @classmethod
    def inflacion_valida(cls, v):
        if v < 0 or v > 100:
            raise ValueError('La inflación mensual debe estar entre 0% y 100%')
        return v


class DetalleCuota(BaseModel):
    mes: int
    cuota_nominal: float
    cuota_valor_presente: float


class InstallmentConvenienceResponseData(BaseModel):
    resultado: str  # "conviene_cuotas" | "conviene_contado" | "indiferente"
    precio_contado: float
    precio_total_cuotas_nominal: float
    costo_real_cuotas: float
    ahorro_real: float
    porcentaje_ahorro: float
    monto_cuota: float
    cantidad_cuotas: int
    inflacion_mensual_usada: float
    detalle_por_mes: list[DetalleCuota]


class IPCData(BaseModel):
    valor_mensual: float
    fecha_dato: str
    es_estimado: bool
    fuente: str
    ultima_actualizacion: datetime
