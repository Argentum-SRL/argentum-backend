from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.billetera import EstadoBilletera
from app.models.usuario import Moneda
from app.schemas.tipos import DecimalJSON


class BilleteraBase(BaseModel):
    usuario_id: UUID | None = None
    nombre: str = Field(..., min_length=1, max_length=100)
    moneda: Moneda
    saldo_actual: DecimalJSON = Field(default=Decimal("0"), decimal_places=2, max_digits=15)
    saldo_inicial: DecimalJSON = Field(default=Decimal("0"), ge=0, decimal_places=2, max_digits=15)
    es_principal: bool = False
    es_efectivo: bool = False
    es_inversion: bool = False
    tna: DecimalJSON | None = Field(default=None, ge=Decimal("0"), decimal_places=2, max_digits=6)
    fecha_ultimo_rendimiento: datetime | None = None
    estado: EstadoBilletera = EstadoBilletera.ACTIVA
    bank_id: str | None = Field(default=None, max_length=50)

    @field_validator("nombre")
    @classmethod
    def validate_nombre(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("El nombre de la billetera no puede estar vacío.")
        return v_clean


class BilleteraCreate(BilleteraBase):
    pass


class BilleteraUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    moneda: Moneda | None = None
    es_principal: bool | None = None
    es_efectivo: bool | None = None
    es_inversion: bool | None = None
    tna: Decimal | None = Field(default=None, ge=Decimal("0"), decimal_places=2, max_digits=6)
    estado: EstadoBilletera | None = None

    @field_validator("nombre")
    @classmethod
    def validate_nombre(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("El nombre de la billetera no puede estar vacío.")
        return v_clean


class BilleteraRead(BilleteraBase):
    id: UUID
    fecha_creacion: datetime
    tiene_transacciones: bool = False

    model_config = ConfigDict(from_attributes=True)


class RendimientoEstimadoResponse(BaseModel):
    billetera_id: UUID
    tiene_tna: bool
    tna: DecimalJSON | None = None
    saldo_actual: DecimalJSON
    dias_transcurridos: int | None = None
    fecha_ultimo_rendimiento: datetime | None = None
    rendimiento_estimado: DecimalJSON | None = None


class ConfirmarRendimientoRequest(BaseModel):
    monto: Decimal = Field(..., gt=Decimal("0"), decimal_places=2, max_digits=15)
    fecha: datetime | None = None