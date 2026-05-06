from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID
from datetime import datetime, date
from typing import Optional, List
from app.models.usuario import Moneda
from app.models.tarjeta_credito import RedTarjeta, EstadoTarjeta

class TarjetaCreditoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    red: RedTarjeta = RedTarjeta.VISA
    dia_cierre: int = Field(..., ge=1, le=28)
    dia_vencimiento: int = Field(..., ge=1, le=28)
    limite_credito: Optional[float] = None
    moneda: Moneda = Moneda.ARS
    color: Optional[str] = Field(None, pattern="^#[0-9a-fA-F]{6}$")

class TarjetaCreditoCreate(TarjetaCreditoBase):
    billetera_id: UUID

class TarjetaCreditoUpdate(BaseModel):
    nombre: Optional[str] = None
    red: Optional[RedTarjeta] = None
    dia_cierre: Optional[int] = Field(None, ge=1, le=28)
    dia_vencimiento: Optional[int] = Field(None, ge=1, le=28)
    limite_credito: Optional[float] = None
    moneda: Optional[Moneda] = None
    estado: Optional[EstadoTarjeta] = None
    color: Optional[str] = Field(None, pattern="^#[0-9a-fA-F]{6}$")

class CuotaResumen(BaseModel):
    id: UUID
    descripcion: str
    numero_cuota: int
    total_cuotas: int
    monto: float
    moneda: Moneda
    fecha_vencimiento: date

class ResumenFuturo(BaseModel):
    mes: str
    total: float
    moneda: Moneda
    cantidad_cuotas: int

class ResumenTarjeta(BaseModel):
    fecha_cierre_proximo: date
    fecha_vencimiento_proximo: date
    total_comprometido_resumen_actual: float
    total_comprometido_resumen_siguiente: float
    cuotas_resumen_actual: List[CuotaResumen]
    cuotas_resumen_siguiente: List[CuotaResumen]
    resumenes_futuros: List[ResumenFuturo]
    limite_credito: Optional[float] = None

class TarjetaCreditoResponse(TarjetaCreditoBase):
    id: UUID
    usuario_id: UUID
    billetera_id: UUID
    estado: EstadoTarjeta
    fecha_creacion: datetime
    resumen_actual: Optional[ResumenTarjeta] = None

    model_config = ConfigDict(from_attributes=True)
