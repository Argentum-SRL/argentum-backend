from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, field_validator, model_validator


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


class CanAffordRequest(BaseModel):
    precio_total: float
    modo: str
    cantidad_cuotas: int = 1
    tiene_interes: bool = False
    tna: float | None = None
    ingreso_manual: float | None = None

    @field_validator('precio_total')
    @classmethod
    def precio_positivo(cls, v):
        if v <= 0:
            raise ValueError('El precio debe ser mayor a 0')
        return v

    @field_validator('modo')
    @classmethod
    def modo_valido(cls, v):
        if v not in ['contado', 'cuotas']:
            raise ValueError('El modo debe ser "contado" o "cuotas"')
        return v

    @field_validator('cantidad_cuotas')
    @classmethod
    def cuotas_validas(cls, v):
        if v < 1 or v > 60:
            raise ValueError('La cantidad de cuotas debe estar entre 1 y 60')
        return v

    @model_validator(mode='after')
    def validar_interes(self) -> CanAffordRequest:
        if self.tiene_interes and self.tna is None:
            raise ValueError('Debe ingresar la TNA si las cuotas tienen interés')
        if self.tna is not None and (self.tna <= 0 or self.tna > 3000):
            raise ValueError('La TNA debe estar entre 0 y 3000%')
        return self


class CanAffordContadoData(BaseModel):
    modo: str
    precio_total: float
    saldo_disponible_actual: float
    saldo_restante_post_compra: float
    porcentaje_del_saldo: float
    porcentaje_del_ingreso_mensual: float | None = None
    semaforo: str
    mensaje_principal: str
    ingreso_promedio_usado: float | None = None
    ingreso_es_manual: bool


class CanAffordCuotasData(BaseModel):
    modo: str
    precio_total: float
    monto_cuota: float
    cantidad_cuotas: int
    carga_mensual_previa: float
    carga_mensual_nueva_total: float
    porcentaje_carga_sobre_ingreso: float | None = None
    margen_libre_post_compra: float | None = None
    semaforo: str
    mensaje_principal: str
    ingreso_promedio_usado: float | None = None
    ingreso_es_manual: bool
    gasto_variable_promedio: float
    tiene_interes: bool
    tna_usada: float | None
    precio_total_real: float
    interes_total: float


class CanAffordResponse(BaseModel):
    success: bool
    data: CanAffordContadoData | CanAffordCuotasData


class FinancialContextResponseData(BaseModel):
    saldo_disponible: float
    ingreso_promedio_mensual: float | None = None
    ingreso_es_estimacion_parcial: bool
    carga_mensual_comprometida: float
    gasto_promedio_variable: float
    ciclos_con_historia: int
    margen_libre_mensual: float | None = None


class FinancialContextResponse(BaseModel):
    success: bool
    data: FinancialContextResponseData

