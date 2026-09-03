from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
)
from app.models.usuario import Moneda
from app.schemas.subcategoria import SubcategoriaRead


class InfoCuotas(BaseModel):
    cantidad_cuotas: int = Field(ge=1, le=120, description="Cantidad total de cuotas (1 a 120)")
    cuota_inicial: int = Field(default=1, ge=1, le=120, description="Número de cuota inicial")
    tiene_interes: bool = False
    tasa_interes: Decimal | None = Field(default=None, ge=0, le=1000, description="Tasa de interés mensual %")
    monto_total: Decimal = Field(gt=0, max_digits=15, decimal_places=2, description="Monto base total")
    proximo_resumen: bool = False

    @model_validator(mode="after")
    def validar_cuotas(self) -> InfoCuotas:
        if self.cuota_inicial > self.cantidad_cuotas:
            raise ValueError("La cuota inicial no puede ser mayor a la cantidad total de cuotas.")
        if self.tiene_interes and self.tasa_interes is not None and self.tasa_interes < 0:
            raise ValueError("La tasa de interés no puede ser negativa.")
        if self.tasa_interes is not None and self.tasa_interes > Decimal("1000"):
            raise ValueError("La tasa de interés debe estar entre 0 y 1000% mensual.")
        return self


class TransaccionBase(BaseModel):
    tipo: TipoTransaccion
    monto: Decimal = Field(gt=0, max_digits=15, decimal_places=2, description="Monto mayor a 0")
    moneda: Moneda
    fecha: date
    descripcion: str = Field(default="", max_length=300)
    categoria_id: UUID
    subcategoria_id: UUID | None = None
    metodo_pago: MetodoPago
    billetera_id: UUID
    tarjeta_id: UUID | None = None
    primer_vencimiento_manual: date | None = None
    es_recurrente: bool = False
    recurrente_id: UUID | None = None
    es_cuota_hija: bool = False
    es_padre_cuotas: bool = False
    grupo_cuotas_id: UUID | None = None
    origen: OrigenTransaccion = OrigenTransaccion.MANUAL
    estado_verificacion: EstadoVerificacionTransaccion | None = None
    pago_resumen_vencimiento: date | None = None
    # Campos de trazabilidad de conversión multimoneda (Etapa 2 y 3C)
    monto_original: Decimal | None = None
    moneda_original: Moneda | None = None
    cotizacion_aplicada: Decimal | None = None
    tipo_dolar_usado: str | None = None
    pago_origen_id: UUID | None = None


class TransaccionCreate(TransaccionBase):
    info_cuotas: InfoCuotas | None = None

    @field_validator("descripcion")
    @classmethod
    def validar_descripcion(cls, v: str) -> str:
        return v.strip() if v else ""


class TransaccionUpdate(BaseModel):
    tipo: TipoTransaccion | None = None
    monto: Decimal | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    moneda: Moneda | None = None
    fecha: date | None = None
    descripcion: str | None = Field(default=None, max_length=300)
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    metodo_pago: MetodoPago | None = None
    billetera_id: UUID | None = None
    tarjeta_id: UUID | None = None
    primer_vencimiento_manual: date | None = None
    es_recurrente: bool | None = None
    estado_verificacion: EstadoVerificacionTransaccion | None = None
    pago_resumen_vencimiento: date | None = None
    pago_origen_id: UUID | None = None

    @field_validator("descripcion")
    @classmethod
    def validar_descripcion_update(cls, v: str | None) -> str | None:
        if v is not None:
            return v.strip()
        return v


class TransaccionRead(TransaccionBase):
    id: UUID
    usuario_id: UUID
    categoria_id: UUID | None = None
    metodo_pago: MetodoPago | None = None
    fecha_creacion: datetime
    subcategoria: SubcategoriaRead | None = None

    model_config = ConfigDict(from_attributes=True)