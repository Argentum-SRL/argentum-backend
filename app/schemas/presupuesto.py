from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator



class PresupuestoCategoriaInput(BaseModel):
    categoria_id: Optional[UUID] = None
    subcategoria_id: Optional[UUID] = None

    @model_validator(mode="after")
    def check_at_least_one(self) -> "PresupuestoCategoriaInput":
        if self.categoria_id is None and self.subcategoria_id is None:
            raise ValueError("Debe proporcionar al menos una categoría o subcategoría")
        return self


class PresupuestoCreate(BaseModel):
    nombre: str = Field(..., min_length=1)
    monto: Decimal = Field(..., gt=0)
    moneda: str # ARS o USD
    periodo: str # semanal, quincenal, mensual
    renovacion: str # automatica, manual
    categorias: List[PresupuestoCategoriaInput] = Field(..., min_length=1)


class PresupuestoUpdate(BaseModel):
    nombre: Optional[str] = None
    monto: Optional[Decimal] = None
    moneda: Optional[str] = None
    renovacion: Optional[str] = None
    categorias: Optional[List[PresupuestoCategoriaInput]] = None


class PresupuestoCategoriaResponse(BaseModel):
    categoria_id: Optional[UUID]
    subcategoria_id: Optional[UUID]
    nombre: str
    es_subcategoria: bool


class PeriodoPresupuestoResponse(BaseModel):
    id: UUID
    presupuesto_id: UUID
    fecha_inicio: date
    fecha_fin: date
    monto_limite: Decimal
    monto_usado: Decimal
    superado: bool
    porcentaje_usado: float
    dias_restantes: int


class PresupuestoResponse(BaseModel):
    id: UUID
    usuario_id: UUID
    nombre: str
    monto: Decimal
    moneda: str
    periodo: str
    renovacion: str
    estado: str
    fecha_creacion: datetime
    categorias: List[PresupuestoCategoriaResponse]
    periodo_actual: Optional[PeriodoPresupuestoResponse] = None
    proxima_renovacion: Optional[date] = None