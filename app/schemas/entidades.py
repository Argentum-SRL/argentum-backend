"""
Schemas para el catálogo de entidades bancarias/billeteras y tasas de interés.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field

from app.schemas.tipos import DecimalJSON


class OpcionTasaEntidad(BaseModel):
    clave: str
    tipo: str = Field(description="'cuenta' o 'fci'")
    etiqueta: str = Field(description="Etiqueta visible de la opción")
    tna: Optional[DecimalJSON] = Field(default=None, description="Tasa Nominal Anual en porcentaje (ej: 19.00)")
    tope: Optional[DecimalJSON] = Field(default=None, description="Tope remunerable si aplica")
    condiciones: Optional[str] = Field(default=None, description="Condiciones del nivel de tasa")
    fecha_dato: Optional[date] = Field(default=None, description="Fecha del dato reportado")
    vieja: bool = Field(default=False, description="True si el dato tiene más de 7 días de antigüedad")


class EntidadResponse(BaseModel):
    id: str
    nombre: str
    tipo_fuente: Optional[str] = Field(default=None, description="'cuenta', 'fci' o None")
    clave_base: Optional[str] = Field(default=None, description="Clave de la tasa base si aplica")
    opciones: List[OpcionTasaEntidad] = Field(default_factory=list, description="Opciones y niveles de tasa disponibles")


class EstimacionRendimientoResponse(BaseModel):
    entidad_id: Optional[str] = None
    tna: Optional[DecimalJSON] = None
    origen: Optional[str] = None
    clave: Optional[str] = None
    fecha_dato: Optional[date] = None
    vieja: bool = False
    tope: Optional[DecimalJSON] = None
    por_dia: Optional[DecimalJSON] = None
    por_mes: Optional[DecimalJSON] = None

