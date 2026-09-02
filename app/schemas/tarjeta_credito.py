from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.models.tarjeta_credito import RedTarjeta, EstadoTarjeta
from app.models.usuario import Moneda

class TarjetaCreditoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    red: RedTarjeta
    dia_cierre: int = Field(..., ge=1, le=28)
    dia_vencimiento: int = Field(..., ge=1, le=28)
    limite_credito: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    moneda: Moneda = Moneda.ARS
    color: str | None = Field(None, max_length=7)

    @field_validator("nombre")
    @classmethod
    def validar_nombre(cls, v: str) -> str:
        nombre = v.strip()
        if not nombre:
            raise ValueError("El nombre de la tarjeta no puede estar vacío.")
        return nombre

class TarjetaCreditoCreate(TarjetaCreditoBase):
    billetera_id: UUID

class TarjetaCreditoUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    red: RedTarjeta | None = None
    dia_cierre: int | None = Field(None, ge=1, le=28)
    dia_vencimiento: int | None = Field(None, ge=1, le=28)
    limite_credito: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    moneda: Moneda | None = None
    color: str | None = Field(None, max_length=7)

    @field_validator("nombre")
    @classmethod
    def validar_nombre(cls, v: str | None) -> str | None:
        if v is None:
            return v
        nombre = v.strip()
        if not nombre:
            raise ValueError("El nombre de la tarjeta no puede estar vacío.")
        return nombre

class CuotaResumen(BaseModel):
    id: UUID
    descripcion: str
    subcategoria_nombre: str | None = None
    numero_cuota: int
    total_cuotas: int
    monto: Decimal
    moneda: str
    fecha_vencimiento: date
    pagada: bool

    class Config:
        from_attributes = True

class ResumenAnterior(BaseModel):
    mes: str
    fecha_vencimiento: date
    fecha_cierre: date
    total: Decimal
    moneda: str
    pagado: bool
    cuotas: list[CuotaResumen]

class ResumenFuturo(BaseModel):
    mes: str           # "Junio 2026"
    mes_fecha: date    # primer día del mes, para ordenar
    total: Decimal
    moneda: str
    cantidad_cuotas: int
    cuotas: list[CuotaResumen] = []

class ResumenTarjeta(BaseModel):
    fecha_cierre_proximo: date
    fecha_vencimiento_proximo: date
    total_comprometido_resumen_actual: Decimal
    total_comprometido_resumen_siguiente: Decimal
    total_actual_ars: Decimal = Decimal("0")
    total_actual_usd: Decimal = Decimal("0")
    total_siguiente_ars: Decimal = Decimal("0")
    total_siguiente_usd: Decimal = Decimal("0")
    totales_moneda_actual: dict[str, Decimal] = Field(default_factory=dict)
    totales_moneda_siguiente: dict[str, Decimal] = Field(default_factory=dict)
    cuotas_resumen_actual: list[CuotaResumen]
    cuotas_resumen_siguiente: list[CuotaResumen]
    resumenes_futuros: list[ResumenFuturo]
    resumenes_anteriores: list[ResumenAnterior] = []


class CuotaPendienteOtraMoneda(BaseModel):
    id: UUID
    descripcion: str
    monto: Decimal
    moneda: str
    numero_cuota: int
    total_cuotas: int
    fecha_vencimiento: date


class ResultadoPagoTarjeta(BaseModel):
    id: UUID
    usuario_id: UUID
    tipo: str
    monto: Decimal
    moneda: str
    fecha: date
    descripcion: str
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    metodo_pago: str | None = None
    billetera_id: UUID | None = None
    tarjeta_id: UUID | None = None
    es_recurrente: bool = False
    estado_verificacion: str | None = None
    fecha_creacion: datetime | None = None
    cuotas_pagadas_count: int = 0
    moneda_pagada: str = ""
    monto_pagado: Decimal = Decimal("0")
    cuotas_pendientes_otra_moneda: list[CuotaPendienteOtraMoneda] = []
    mensaje_advertencia: str | None = None

    class Config:
        from_attributes = True

class TarjetaCreditoResponse(TarjetaCreditoBase):
    id: UUID
    usuario_id: UUID
    billetera_id: UUID
    estado: EstadoTarjeta
    fecha_creacion: datetime
    resumen_actual: ResumenTarjeta | None = None

    class Config:
        from_attributes = True


class PagarTarjetaBody(BaseModel):
    fecha_pago: date | None = None
    fecha_resumen: date | None = None


class DetalleTarjetaMes(BaseModel):
    tarjeta_id: str
    tarjeta_nombre: str
    total: float
    moneda: str | None = None


class MesPresionFutura(BaseModel):
    anio: int
    mes: int
    mes_label: str
    total: dict[str, float]
    tarjetas: list[DetalleTarjetaMes]


class PresionFuturaData(BaseModel):
    meses: list[MesPresionFutura]
    total_comprometido: dict[str, float]

    class Config:
        from_attributes = True


class PresionFuturaResponse(BaseModel):
    success: bool
    data: PresionFuturaData

    class Config:
        from_attributes = True

