from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.tipos import DecimalJSON

class GrupoCuotasResumen(BaseModel):
    id: UUID
    descripcion: str
    monto_total: DecimalJSON
    total_financiado: DecimalJSON
    cantidad_cuotas: int
    cantidad_pagadas: int
    cantidad_pendientes: int
    monto_cuota: DecimalJSON
    proximo_vencimiento: date | None = None
    total_pagado: DecimalJSON
    total_pendiente: DecimalJSON
    moneda: str
    tarjeta_nombre: str | None = None
    fecha_compra: date
    transaccion_padre_id: UUID
    tiene_interes: bool
    tasa_interes: DecimalJSON | None = None
    estado: str = "activo"
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None

    model_config = ConfigDict(from_attributes=True)

class GrupoCuotasUpdate(BaseModel):
    monto_total_nuevo: DecimalJSON | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    descripcion: str | None = Field(default=None, max_length=300)
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    tarjeta_id: UUID | None = None
    billetera_id: UUID | None = None
    fecha_referencia: date | None = None

    @field_validator("descripcion")
    @classmethod
    def validar_desc(cls, v: str | None) -> str | None:
        if v is not None:
            return v.strip()
        return v
