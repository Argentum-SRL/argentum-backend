from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, ConfigDict

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

    model_config = ConfigDict(from_attributes=True)

class GrupoCuotasUpdate(BaseModel):
    monto_total_nuevo: Decimal | None = None
    descripcion: str | None = None
