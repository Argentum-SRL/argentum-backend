from datetime import datetime
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

class TarjetaCreditoResponse(TarjetaCreditoBase):
    id: UUID
    usuario_id: UUID
    billetera_id: UUID
    estado: EstadoTarjeta
    fecha_creacion: datetime

    class Config:
        from_attributes = True
