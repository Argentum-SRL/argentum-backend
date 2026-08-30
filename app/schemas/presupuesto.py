from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator



from app.models.usuario import Moneda
from app.models.presupuesto import PeriodoPresupuestoTipo, RenovacionPresupuesto


class PresupuestoCategoriaInput(BaseModel):
    categoria_id: Optional[UUID] = None
    subcategoria_id: Optional[UUID] = None

    @field_validator("categoria_id", "subcategoria_id", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        if v == "" or v is None:
            return None
        return v

    @model_validator(mode="after")
    def check_at_least_one(self) -> "PresupuestoCategoriaInput":
        if self.categoria_id is None and self.subcategoria_id is None:
            raise ValueError("Debe proporcionar al menos una categoría o subcategoría")
        return self


class PresupuestoCreate(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    monto: Decimal = Field(..., gt=0, le=Decimal("999999999999.99"))
    moneda: Moneda
    periodo: PeriodoPresupuestoTipo
    renovacion: RenovacionPresupuesto
    categorias: List[PresupuestoCategoriaInput] = Field(..., min_length=1)

    @field_validator("nombre")
    @classmethod
    def validate_nombre(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("El nombre del presupuesto no puede estar vacío")
        if len(cleaned) > 100:
            raise ValueError("El nombre no puede superar los 100 caracteres")
        return cleaned

    @field_validator("monto")
    @classmethod
    def validate_monto(cls, v: Decimal) -> Decimal:
        if v is not None:
            if v <= 0:
                raise ValueError("El monto límite debe ser mayor a cero")
            if v.as_tuple().exponent < -2:
                raise ValueError("El monto no puede tener más de 2 decimales")
        return v

    @field_validator("categorias")
    @classmethod
    def validate_categorias(cls, v: List[PresupuestoCategoriaInput]) -> List[PresupuestoCategoriaInput]:
        if not v:
            raise ValueError("Debe seleccionar al menos una categoría")
        seen = set()
        deduped = []
        for cat in v:
            key = (cat.categoria_id, cat.subcategoria_id)
            if key not in seen:
                seen.add(key)
                deduped.append(cat)
        return deduped


class PresupuestoUpdate(BaseModel):
    nombre: Optional[str] = Field(None, max_length=100)
    monto: Optional[Decimal] = Field(None, gt=0, le=Decimal("999999999999.99"))
    moneda: Optional[Moneda] = None
    periodo: Optional[PeriodoPresupuestoTipo] = None
    renovacion: Optional[RenovacionPresupuesto] = None
    categorias: Optional[List[PresupuestoCategoriaInput]] = None

    @field_validator("nombre")
    @classmethod
    def validate_nombre_update(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("El nombre del presupuesto no puede estar vacío")
        if len(cleaned) > 100:
            raise ValueError("El nombre no puede superar los 100 caracteres")
        return cleaned

    @field_validator("monto")
    @classmethod
    def validate_monto_update(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None:
            if v <= 0:
                raise ValueError("El monto límite debe ser mayor a cero")
            if v.as_tuple().exponent < -2:
                raise ValueError("El monto no puede tener más de 2 decimales")
        return v

    @field_validator("categorias")
    @classmethod
    def validate_categorias_update(cls, v: Optional[List[PresupuestoCategoriaInput]]) -> Optional[List[PresupuestoCategoriaInput]]:
        if v is None:
            return None
        if not v:
            raise ValueError("Debe seleccionar al menos una categoría")
        seen = set()
        deduped = []
        for cat in v:
            key = (cat.categoria_id, cat.subcategoria_id)
            if key not in seen:
                seen.add(key)
                deduped.append(cat)
        return deduped


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