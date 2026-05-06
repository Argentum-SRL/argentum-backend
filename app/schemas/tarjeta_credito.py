from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field
from app.models.tarjeta_credito import RedTarjeta, EstadoTarjeta
from app.models.usuario import Moneda

class TarjetaCreditoBase(BaseModel):
    nombre: str
    red: RedTarjeta
    dia_cierre: int = Field(..., ge=1, le=28)
    dia_vencimiento: int = Field(..., ge=1, le=28)
    limite_credito: Decimal | None = None
    moneda: Moneda = Moneda.ARS
    color: str | None = Field(None, max_length=7)

class TarjetaCreditoCreate(TarjetaCreditoBase):
    billetera_id: UUID

class TarjetaCreditoUpdate(BaseModel):
    nombre: str | None = None
    red: RedTarjeta | None = None
    dia_cierre: int | None = Field(None, ge=1, le=28)
    dia_vencimiento: int | None = Field(None, ge=1, le=28)
    limite_credito: Decimal | None = None
    moneda: Moneda | None = None
    color: str | None = Field(None, max_length=7)

class CuotaResumen(BaseModel):
    id: UUID
    descripcion: str
    subcategoria_nombre: str | None = None
    numero_cuota: int
    total_cuotas: int
    monto: Decimal
    moneda: str
    fecha_vencimiento: date

    class Config:
        from_attributes = True

class ResumenFuturo(BaseModel):
    mes: str           # "Junio 2026"
    mes_fecha: date    # primer día del mes, para ordenar
    total: Decimal
    moneda: str
    cantidad_cuotas: int

class ResumenTarjeta(BaseModel):
    fecha_cierre_proximo: date
    fecha_vencimiento_proximo: date
    total_comprometido_resumen_actual: Decimal
    total_comprometido_resumen_siguiente: Decimal
    cuotas_resumen_actual: list[CuotaResumen]
    cuotas_resumen_siguiente: list[CuotaResumen]
    resumenes_futuros: list[ResumenFuturo]

class TarjetaCreditoResponse(TarjetaCreditoBase):
    id: UUID
    usuario_id: UUID
    billetera_id: UUID
    estado: EstadoTarjeta
    fecha_creacion: datetime
    resumen_actual: ResumenTarjeta | None = None

    class Config:
        from_attributes = True
