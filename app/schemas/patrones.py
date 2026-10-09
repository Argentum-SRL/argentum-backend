"""
app/schemas/patrones.py — Schemas Pydantic para el módulo de patrones (Lo que se repite).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DecisionPatronCreate(BaseModel):
    """Schema para registrar o actualizar una decisión de patrón."""
    clave_item: str
    decision: str  # "confirmar", "descartar", "mover"
    caja_destino: Optional[str] = None  # "fijo", "costumbre", "dia_a_dia"


class DeshacerDecisionRequest(BaseModel):
    """Schema para deshacer una decisión tomada sobre un ítem."""
    clave_item: str


class ItemRepetidoRead(BaseModel):
    """Representa un ítem de gasto detectado o reclasificado (fijo, costumbre o día a día)."""
    model_config = ConfigDict(from_attributes=True)

    clave_item: str
    nombre: str
    rubro: Optional[str] = None
    moneda: str
    caja_detectada: str
    caja: str
    estado: str  # "sugerido", "confirmado", "movido"
    frecuencia: Optional[str] = None
    dia_tipico: Optional[int] = None
    proxima_fecha: Optional[date] = None
    fuerza: Optional[str] = None
    ocurrencias: int
    monto_tipico: Decimal
    ultimo_monto: Optional[Decimal] = None
    monto_mensual: Decimal
    cuenta_en_numeros: bool
    transacciones_ids: List[UUID]


class ItemIngresoRead(BaseModel):
    """Representa un ítem de ingreso habitual detectado."""
    model_config = ConfigDict(from_attributes=True)

    clave_item: str
    nombre: str
    tipo: str  # "regular", "intermitente", "variable"
    moneda: str
    monto_mensual: Decimal
    estado: str  # "sugerido", "confirmado"
    cuenta_en_numeros: bool
    editable: bool


class PatronesResumenResponse(BaseModel):
    """Respuesta completa del servicio 'Lo que se repite'."""
    fecha_calculo: date
    meses_ventana: int
    ingresos: List[ItemIngresoRead]
    fijos: List[ItemRepetidoRead]
    costumbre: List[ItemRepetidoRead]
    dia_a_dia: List[ItemRepetidoRead]
