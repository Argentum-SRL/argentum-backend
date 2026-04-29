from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field
from app.models.usuario import CicloTipo, Moneda
from app.schemas.usuario import UsuarioRead

class DatosActuales(BaseModel):
    nombre: str | None = None
    apellido: str | None = None
    moneda_principal: str | None = None
    ciclo_tipo: str | None = None
    ciclo_valor: str | None = None

class EstadoOnboardingResponse(BaseModel):
    onboarding_completo: bool
    pasos_pendientes: list[str]
    datos_actuales: DatosActuales

class DatosPersonalesRequest(BaseModel):
    nombre: str = Field(..., min_length=1)
    apellido: str = Field(..., min_length=1)

class CicloFinancieroRequest(BaseModel):
    ciclo_tipo: CicloTipo
    ciclo_valor: str

class MonedaRequest(BaseModel):
    moneda_principal: Moneda
    moneda_secundaria_activa: bool
    tipo_dolar: str | None = None

class PrimeraBilleteraRequest(BaseModel):
    nombre: str
    moneda: Moneda
    saldo_inicial: float = 0.0

class OnboardingStepResponse(BaseModel):
    completated: bool = Field(..., alias="completado")
    siguiente_paso: str | None = None

    class Config:
        populate_by_name = True

class FinalizarOnboardingResponse(BaseModel):
    completated: bool = Field(..., alias="completado")
    usuario: UsuarioRead

    class Config:
        populate_by_name = True
