from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.constants import MAX_MONTO_INTEGRIDAD


class TransaccionCruda(BaseModel):
    """
    Representa una transacción cruda extraída del resumen de tarjeta.
    
    Campos:
        fecha (date): Fecha de realización de la transacción.
        descripcion (str): Nombre del comercio o descripción de la transacción limpia de caracteres prefijos.
        monto (Decimal): Monto de la transacción (positivo para consumos/cargos, negativo para créditos/devoluciones).
        moneda (str): Moneda de la transacción ('ARS' o 'USD').
        cuota_actual (int | None): Número de la cuota actual, en caso de consumos cuotificados.
        cuota_total (int | None): Cantidad total de cuotas, en caso de consumos cuotificados.
        es_cargo_bancario (bool): Indica si la transacción es un cargo directo del banco (por ejemplo, impuesto de sellos).
        titular_seccion (str | None): Nombre del titular de la tarjeta que realizó la compra (usado por ciertos bancos).
        categoria_id (UUID | None): Identificador de la categoría asignada por el usuario.
    """
    fecha: date
    descripcion: str
    monto: Decimal
    moneda: str
    cuota_actual: int | None = None
    cuota_total: int | None = None
    es_cargo_bancario: bool = False
    titular_seccion: str | None = None
    categoria_id: UUID | None = None

    @field_validator('descripcion')
    @classmethod
    def validar_descripcion(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError('La descripción de la transacción no puede estar vacía')
        return v_clean

    @field_validator('moneda')
    @classmethod
    def validar_moneda(cls, v: str) -> str:
        v_upper = v.upper().strip()
        if v_upper not in ('ARS', 'USD'):
            raise ValueError('La moneda debe ser ARS o USD')
        return v_upper

    @field_validator('monto')
    @classmethod
    def validar_monto(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError('El monto de la transacción no puede ser cero')
        if abs(v) > MAX_MONTO_INTEGRIDAD:
            raise ValueError('El monto de la transacción excede el límite permitido')
        return v

    @field_validator('cuota_actual')
    @classmethod
    def validar_cuota_actual(cls, v: int | None) -> int | None:
        if v is not None and (v < 1 or v > 120):
            raise ValueError('La cuota actual debe estar entre 1 y 120')
        return v

    @field_validator('cuota_total')
    @classmethod
    def validar_cuota_total(cls, v: int | None) -> int | None:
        if v is not None and (v < 1 or v > 120):
            raise ValueError('La cantidad total de cuotas debe estar entre 1 y 120')
        return v

    @model_validator(mode='after')
    def validar_coherencia_cuotas(self) -> TransaccionCruda:
        if self.cuota_actual is not None and self.cuota_total is not None:
            if self.cuota_actual > self.cuota_total:
                self.cuota_total = self.cuota_actual
        elif self.cuota_actual is not None and self.cuota_total is None:
            self.cuota_total = self.cuota_actual
        return self


class ResultadoParseo(BaseModel):
    """
    Representa el resultado consolidado del parseo de un resumen de tarjeta.
    
    Campos:
        banco (str): Identificador del banco emisor detectado ('galicia', 'bna_visa', etc.).
        titular_detectado (str | None): Nombre del titular del resumen de la tarjeta.
        ultimos_4_digitos (str | None): Últimos 4 dígitos de la tarjeta de crédito principal.
        periodo_desde (date | None): Fecha de inicio del período de facturación del resumen.
        periodo_hasta (date | None): Fecha de fin del período de facturación del resumen.
        transacciones (list[TransaccionCruda]): Lista de transacciones individuales parseadas con éxito.
        confianza (float): Nivel de confianza del resultado (0.0 significa error/ilegible, 1.0 significa máxima certeza).
        capa_usada (str): Identificador de la capa de procesamiento utilizada ('deterministic', etc.).
        escalado (bool): Indica si se aplicó alguna lógica de fallback/escalado durante el procesamiento.
    """
    banco: str
    titular_detectado: str | None = None
    ultimos_4_digitos: str | None = None
    periodo_desde: date | None = None
    periodo_hasta: date | None = None
    transacciones: list[TransaccionCruda] = Field(default_factory=list)
    confianza: float
    capa_usada: str
    escalado: bool = False
