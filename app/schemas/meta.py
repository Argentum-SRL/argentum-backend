from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.meta import EstadoMeta
from app.models.usuario import Moneda
from app.schemas.movimiento_meta import MovimientoMetaRead


class MetaBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    monto_objetivo: Decimal = Field(..., gt=Decimal("0"), le=Decimal("9999999999999.99"))
    moneda: Moneda
    monto_actual: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), le=Decimal("9999999999999.99"))
    fecha_limite: date | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    nota: str | None = Field(default=None, max_length=1000)
    estado: EstadoMeta = EstadoMeta.ACTIVA

    @field_validator("nombre")
    @classmethod
    def validar_nombre(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("El nombre de la meta no puede estar vacío.")
        if len(s) > 100:
            raise ValueError("El nombre de la meta no puede superar los 100 caracteres.")
        return s

    @field_validator("monto_objetivo", "monto_actual")
    @classmethod
    def validar_monto(cls, v: Decimal) -> Decimal:
        if v.as_tuple().exponent < -2:
            raise ValueError("El monto no puede tener más de 2 decimales.")
        return v

    @field_validator("nota")
    @classmethod
    def validar_nota(cls, v: str | None) -> str | None:
        if v is not None:
            s = v.strip()
            return s if s else None
        return None


class MetaCreate(MetaBase):
    pass


class MetaUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    monto_objetivo: Decimal | None = Field(default=None, gt=Decimal("0"), le=Decimal("9999999999999.99"))
    moneda: Moneda | None = None
    fecha_limite: date | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    nota: str | None = Field(default=None, max_length=1000)
    estado: EstadoMeta | None = None

    @field_validator("nombre")
    @classmethod
    def validar_nombre(cls, v: str | None) -> str | None:
        if v is not None:
            s = v.strip()
            if not s:
                raise ValueError("El nombre de la meta no puede estar vacío.")
            if len(s) > 100:
                raise ValueError("El nombre de la meta no puede superar los 100 caracteres.")
            return s
        return None

    @field_validator("monto_objetivo")
    @classmethod
    def validar_monto_objetivo(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v <= Decimal("0"):
                raise ValueError("El monto objetivo tiene que ser mayor a cero.")
            if v.as_tuple().exponent < -2:
                raise ValueError("El monto no puede tener más de 2 decimales.")
        return v

    @field_validator("nota")
    @classmethod
    def validar_nota(cls, v: str | None) -> str | None:
        if v is not None:
            s = v.strip()
            return s if s else None
        return None


class MetaRead(MetaBase):
    id: UUID
    usuario_id: UUID
    fecha_creacion: datetime
    movimientos: List[MovimientoMetaRead] = []

    model_config = ConfigDict(from_attributes=True)


class GoalAnalyticsResponse(BaseModel):
    chart_data: List[Dict[str, Any]]
    velocidad_mensual: float
    meses_restantes: float | None
    fecha_estimada_finalizacion: date | None
    porcentaje_progreso: float
    monto_faltante: float


class GoalSummaryResponse(BaseModel):
    total_metas: int
    completadas: int
    proximo_vencimiento: date | None