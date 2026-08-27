from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.usuario import Moneda


class TransferenciaInternaBase(BaseModel):
    usuario_id: UUID | None = None
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


class TransferenciaInternaCreate(TransferenciaInternaBase):
    pass


class TransferenciaInternaUpdate(BaseModel):
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


class TransferenciaInternaRead(TransferenciaInternaBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)