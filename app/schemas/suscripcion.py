from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import List, Optional

class HistorialSuscripcionResponse(BaseModel):
    id: UUID
    monto: Decimal
    moneda: str
    vigente_desde: date
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)

class SuscripcionBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    categoria_id: Optional[UUID] = None
    subcategoria_id: Optional[UUID] = None
    frecuencia: str = Field(..., pattern="^(mensual|bimestral|trimestral|semestral|anual)$")
    proximo_cobro: date
    billetera_id: Optional[UUID] = None
    tarjeta_id: Optional[UUID] = None

    @field_validator('nombre')
    @classmethod
    def validar_nombre(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("El nombre de la suscripción no puede estar vacío.")
        if len(s) > 100:
            raise ValueError("El nombre de la suscripción no puede superar los 100 caracteres.")
        return s

class SuscripcionCreate(SuscripcionBase):
    monto: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    moneda: str = Field(default='ARS', pattern="^(ARS|USD)$")
    vigente_desde: Optional[date] = None

    @field_validator('monto')
    @classmethod
    def validar_monto(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El monto debe ser mayor a cero.")
        if v.as_tuple().exponent < -2:
            raise ValueError("El monto no puede tener más de 2 decimales.")
        return v

    @model_validator(mode='after')
    def validar_exclusividad_medio_pago(self) -> SuscripcionCreate:
        if self.billetera_id and self.tarjeta_id:
            raise ValueError("Una suscripción no puede estar vinculada a una billetera y a una tarjeta al mismo tiempo.")
        return self

class SuscripcionUpdate(BaseModel):
    nombre: Optional[str] = Field(None, min_length=1, max_length=100)
    categoria_id: Optional[UUID] = None
    subcategoria_id: Optional[UUID] = None
    frecuencia: Optional[str] = Field(None, pattern="^(mensual|bimestral|trimestral|semestral|anual)$")
    proximo_cobro: Optional[date] = None
    billetera_id: Optional[UUID] = None
    tarjeta_id: Optional[UUID] = None
    estado: Optional[str] = Field(None, pattern="^(activa|pausada|cancelada)$")
    monto: Optional[Decimal] = Field(None, gt=0, max_digits=15, decimal_places=2)
    moneda: Optional[str] = Field(None, pattern="^(ARS|USD)$")
    vigente_desde: Optional[date] = None

    @field_validator('nombre')
    @classmethod
    def validar_nombre_update(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("El nombre de la suscripción no puede estar vacío.")
            if len(s) > 100:
                raise ValueError("El nombre de la suscripción no puede superar los 100 caracteres.")
            return s
        return v

    @field_validator('monto')
    @classmethod
    def validar_monto_update(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None:
            if v <= 0:
                raise ValueError("El monto debe ser mayor a cero.")
            if v.as_tuple().exponent < -2:
                raise ValueError("El monto no puede tener más de 2 decimales.")
        return v

    @model_validator(mode='after')
    def validar_exclusividad_medio_pago_update(self) -> SuscripcionUpdate:
        if self.billetera_id and self.tarjeta_id:
            raise ValueError("Una suscripción no puede estar vinculada a una billetera y a una tarjeta al mismo tiempo.")
        return self

class ActualizarPrecioRequest(BaseModel):
    monto: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    moneda: str = Field(..., pattern="^(ARS|USD)$")
    vigente_desde: date

    @field_validator('monto')
    @classmethod
    def validar_monto_precio(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El monto debe ser mayor a cero.")
        if v.as_tuple().exponent < -2:
            raise ValueError("El monto no puede tener más de 2 decimales.")
        return v

class SuscripcionResponse(SuscripcionBase):
    id: UUID
    usuario_id: UUID
    estado: str
    fecha_creacion: datetime
    precio_actual: Optional[HistorialSuscripcionResponse] = None
    historial_precios: List[HistorialSuscripcionResponse] = []
    costo_mensual_equivalente: Optional[Decimal] = None

    model_config = ConfigDict(from_attributes=True)

class TotalMensualResponse(BaseModel):
    total_ars: Decimal
    total_usd: Decimal