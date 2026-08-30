from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from dateutil.relativedelta import relativedelta

from app.models.usuario import Moneda
from app.utils.fecha import hoy_argentina


class TransferenciaInternaCreate(BaseModel):
    """Schema para crear una transferencia interna. No incluye usuario_id (se obtiene del token de autenticación)."""
    billetera_origen_id: UUID
    billetera_destino_id: UUID
    monto: Decimal = Field(..., gt=0, decimal_places=2, max_digits=15, description="Monto mayor a 0 con hasta 2 decimales")
    moneda: Moneda
    fecha: date
    notas: str | None = Field(default=None, max_length=300)

    @field_validator("notas")
    @classmethod
    def clean_notas(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_clean = v.strip()
        return v_clean if v_clean else None

    @model_validator(mode="after")
    def validar_fecha(self) -> TransferenciaInternaCreate:
        """Valida que la fecha no sea futura ni demasiado lejana en el pasado."""
        hoy = hoy_argentina()
        
        # No permitir fechas futuras
        if self.fecha > hoy:
            raise ValueError("La fecha de la transferencia no puede ser futura.")
        
        # No permitir fechas más de 2 años atrás
        hace_dos_anos = hoy - relativedelta(years=2)
        if self.fecha < hace_dos_anos:
            raise ValueError("La fecha de la transferencia no puede ser más de 2 años anterior a hoy.")
        
        return self

    @model_validator(mode="after")
    def validar_billeteras_distintas(self) -> TransferenciaInternaCreate:
        """Valida que origen y destino sean billeteras diferentes."""
        if self.billetera_origen_id == self.billetera_destino_id:
            raise ValueError("La billetera de origen y destino no pueden ser la misma.")
        return self


class TransferenciaInternaRead(BaseModel):
    """Schema para leer una transferencia interna. No incluye usuario_id (el usuario autenticado ya lo sabe)."""
    id: UUID
    billetera_origen_id: UUID
    billetera_destino_id: UUID
    monto: Decimal
    moneda: Moneda
    fecha: date
    notas: str | None
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)


class TransferenciaInternaUpdate(BaseModel):
    """Schema para actualizar una transferencia (actualmente no se usa, pero se mantiene para futuras extensiones)."""
    billetera_origen_id: UUID | None = None
    billetera_destino_id: UUID | None = None
    monto: Decimal | None = Field(default=None, gt=0, decimal_places=2, max_digits=15)
    moneda: Moneda | None = None
    fecha: date | None = None
    notas: str | None = Field(default=None, max_length=300)

    @field_validator("notas")
    @classmethod
    def clean_notas(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_clean = v.strip()
        return v_clean if v_clean else None