from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.tipos import DecimalJSON
from app.schemas.transaccion import ReintegroRead, TransaccionRead


class MovimientoItem(BaseModel):
    id: str
    clase: Literal["transaccion", "transferencia", "rendimiento", "ajuste", "saldo_inicial"]
    fecha: date
    descripcion: str
    monto: DecimalJSON = Field(ge=0, description="Monto siempre positivo")
    moneda: str
    sentido: Literal["entra", "sale", "neutro"]
    etiqueta: str
    cuenta_como: Literal["gasto", "ingreso", "nada"]
    aclaracion: Optional[str] = None
    cuenta_en_saldo: bool = False
    saldo_despues: Optional[DecimalJSON] = None
    cuota_texto: Optional[str] = None
    grupo_cuotas_id: Optional[UUID] = None
    billetera_id: Optional[UUID] = None
    billetera_nombre: Optional[str] = None
    billetera_destino_nombre: Optional[str] = None
    monto_destino: Optional[DecimalJSON] = None
    moneda_destino: Optional[str] = None
    transaccion: Optional[TransaccionRead] = None
    reintegro: Optional[ReintegroRead] = None


class TotalesMoneda(BaseModel):
    gastos: DecimalJSON = Decimal("0.00")
    ingresos: DecimalJSON = Decimal("0.00")
    balance: DecimalJSON = Decimal("0.00")


class MovimientosResponse(BaseModel):
    items: List[MovimientoItem]
    hay_mas: bool
    total_items: int
    totales: Dict[str, TotalesMoneda]
