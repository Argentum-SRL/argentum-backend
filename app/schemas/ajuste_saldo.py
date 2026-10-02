from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.usuario import Moneda
from app.schemas.tipos import DecimalJSON


class ActualizarSaldoRequest(BaseModel):
    saldo_declarado: DecimalJSON = Field(..., ge=0, decimal_places=2, max_digits=15)
    rendimiento: DecimalJSON | None = Field(default=None, gt=0, decimal_places=2, max_digits=15)


class AjusteSaldoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    billetera_id: UUID
    monto: DecimalJSON
    saldo_anterior: DecimalJSON
    saldo_declarado: DecimalJSON
    fecha: date
    fecha_creacion: datetime


class CoberturaRead(BaseModel):
    mostrar: bool
    por_cada_100: int | None = None
    desde: date | None = None
    hasta: date | None = None


class AjustesBilleteraResponse(BaseModel):
    ajustes: list[AjusteSaldoRead]
    cobertura: CoberturaRead


class HuecoRead(BaseModel):
    desde: date
    hasta: date
    dias: int


class DuplicadoRead(BaseModel):
    fecha: date
    monto: DecimalJSON
    cantidad: int
    descripciones: list[str]


class ControlSaldoPreview(BaseModel):
    billetera_id: UUID
    moneda: Moneda | str
    saldo_registrado: DecimalJSON
    saldo_declarado: DecimalJSON
    diferencia: DecimalJSON
    rendimiento_propuesto: DecimalJSON | None = None
    es_grande: bool
    salida_semanal_tipica: DecimalJSON | None = None
    semanas_con_historia: int
    ultimo_movimiento: date | None = None
    huecos: list[HuecoRead]
    posibles_duplicados: list[DuplicadoRead]
