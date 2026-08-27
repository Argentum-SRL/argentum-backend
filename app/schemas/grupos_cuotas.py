from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator

class GrupoCuotasResumen(BaseModel):
    id: UUID
    descripcion: str
    monto_total: Decimal
    total_financiado: Decimal
    cantidad_cuotas: int
    cantidad_pagadas: int
    cantidad_pendientes: int
    monto_cuota: Decimal
    proximo_vencimiento: date | None = None
    total_pagado: Decimal
    total_pendiente: Decimal
    moneda: str
    tarjeta_nombre: str | None = None
    fecha_compra: date
    transaccion_padre_id: UUID
    tiene_interes: bool
    tasa_interes: Decimal | None = None
    estado: str = "activo"

    model_config = ConfigDict(from_attributes=True)

class GrupoCuotasUpdate(BaseModel):
    monto_total_nuevo: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    descripcion: str | None = Field(default=None, min_length=1, max_length=300)

    @field_validator("descripcion")
    @classmethod
    def validar_desc(cls, v: str | None) -> str | None:
        if v is not None:
            clean = v.strip()
            if not clean:
                raise ValueError("La descripción no puede estar vacía.")
            return clean
        return v
