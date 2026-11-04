from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, field_validator, model_validator


from app.core.constants import MAX_MONTO_FLOAT

MAX_MONTO = MAX_MONTO_FLOAT


class InstallmentConvenienceRequest(BaseModel):
    precio_contado: float
    precio_total_cuotas: float | None = None
    cantidad_cuotas: int
    inflacion_mensual: float
    tiene_interes: bool = False
    tna: float | None = None
    tna_billetera_inversion: float | None = None

    @field_validator('precio_contado')
    @classmethod
    def precio_contado_positivo(cls, v):
        if v <= 0:
            raise ValueError('El precio de contado debe ser mayor a 0')
        if v > MAX_MONTO:
            raise ValueError('El precio de contado excede el límite permitido')
        return v

    @field_validator('precio_total_cuotas')
    @classmethod
    def precio_cuotas_positivo(cls, v):
        if v is not None:
            if v <= 0:
                raise ValueError('El precio en cuotas debe ser mayor a 0')
            if v > MAX_MONTO:
                raise ValueError('El precio en cuotas excede el límite permitido')
        return v

    @field_validator('cantidad_cuotas')
    @classmethod
    def cuotas_validas(cls, v):
        if v < 1 or v > 120:
            raise ValueError('La cantidad de cuotas debe ser un número entero entre 1 y 120')
        return v

    @field_validator('inflacion_mensual')
    @classmethod
    def inflacion_valida(cls, v):
        if v < 0 or v > 100:
            raise ValueError('La inflación mensual estimada debe estar entre 0% y 100%')
        return v

    @field_validator('tna')
    @classmethod
    def tna_valida(cls, v):
        if v is not None and (v < 0.1 or v > 3000):
            raise ValueError('La TNA debe estar entre 0.1% y 3000%')
        return v

    @field_validator('tna_billetera_inversion')
    @classmethod
    def tna_billetera_valida(cls, v):
        if v is not None and (v < 0.1 or v > 3000):
            raise ValueError('La TNA de la billetera debe estar entre 0.1% y 3000%')
        return v

    @model_validator(mode='after')
    def validar_interes_y_total(self) -> InstallmentConvenienceRequest:
        if not self.tiene_interes:
            if self.precio_total_cuotas is None:
                raise ValueError('Debe ingresar el precio total en cuotas si no tiene interés')
        else:
            if self.tna is None:
                raise ValueError('Debe ingresar la TNA si las cuotas tienen interés')
        return self


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
    tiene_interes: bool = False
    tna_usada: float | None = None
    interes_total: float | None = None
    precio_total_cuotas_con_interes: float | None = None
    tna_billetera_inversion_usada: float | None = None
    saldo_final_inversion: float | None = None
    remanente_vpn: float | None = None


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
            raise ValueError('El precio de la compra debe ser mayor a 0')
        if v > MAX_MONTO:
            raise ValueError('El precio de la compra excede el límite permitido')
        return v

    @field_validator('modo')
    @classmethod
    def modo_valido(cls, v):
        v_norm = str(v).lower().strip()
        if v_norm not in ['contado', 'cuotas', 'mensual']:
            raise ValueError('El modo de pago debe ser "contado", "cuotas" o "mensual"')
        return v_norm

    @field_validator('cantidad_cuotas')
    @classmethod
    def cuotas_validas(cls, v):
        if v < 1 or v > 120:
            raise ValueError('La cantidad de cuotas debe estar entre 1 y 120')
        return v

    @field_validator('ingreso_manual')
    @classmethod
    def ingreso_manual_valido(cls, v):
        if v is not None:
            if v <= 0:
                raise ValueError('El ingreso estimado debe ser mayor a 0')
            if v > MAX_MONTO:
                raise ValueError('El ingreso estimado excede el límite permitido')
        return v

    @field_validator('tna')
    @classmethod
    def tna_valida(cls, v):
        if v is not None and (v < 0.1 or v > 3000):
            raise ValueError('La TNA debe estar entre 0.1% y 3000%')
        return v

    @model_validator(mode='after')
    def validar_interes_y_modo(self) -> CanAffordRequest:
        if self.modo == 'cuotas':
            if self.tiene_interes and self.tna is None:
                raise ValueError('Debe ingresar la TNA si las cuotas tienen interés')
        else:
            self.cantidad_cuotas = 1
            self.tiene_interes = False
            self.tna = None
        return self


class CanAffordAhorrosData(BaseModel):
    inversiones: float
    dolares_usd: float
    dolares_en_pesos: float
    metas: float
    total: float


class CanAffordResponseData(BaseModel):
    modo: str
    resultado: str
    motivo: str | None = None
    titulo: str
    linea_principal: str
    lineas_plan: list[str] = []
    linea_ahorros: str | None = None
    ahorros: CanAffordAhorrosData | None = None
    linea_liberacion: str | None = None
    linea_compensar: str | None = None
    linea_frescura: str | None = None
    libre: float
    precio_total: float
    fuente_inflacion: str | None = None


class CanAffordResponse(BaseModel):
    success: bool
    data: CanAffordResponseData


class InflacionEsperadaData(BaseModel):
    valor_mensual: float
    meses: list[float]
    fuente_texto: str
    es_respaldo: bool


class InflacionEsperadaResponse(BaseModel):
    success: bool
    data: InflacionEsperadaData | None = None


class CurrencyFinancialData(BaseModel):
    total_billeteras: float
    cuotas_comprometidas: float
    suscripciones_mensuales: float
    saldo_disponible: float


class FinancialContextResponseData(BaseModel):
    saldo_disponible: float
    carga_mensual_comprometida: float
    ars: CurrencyFinancialData
    usd: CurrencyFinancialData
    ingreso_promedio_mensual: float | None = None
    ingreso_es_estimacion_parcial: bool
    gasto_promedio_variable: float
    ciclos_con_historia: int
    margen_libre_mensual: float | None = None


class FinancialContextResponse(BaseModel):
    success: bool
    data: FinancialContextResponseData

