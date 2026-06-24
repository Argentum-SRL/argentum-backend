from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AnalisisIACreate(BaseModel):
    tipo_analisis: Literal['completo', 'gastos_hormiga', 'suscripciones', 'fondo_emergencia'] = 'completo'
    ciclos: int = Field(default=3, ge=2, le=6)


class SeccionesAnalisis(BaseModel):
    resumen_ejecutivo: Optional[str] = None
    salud_financiera: Optional[str] = None
    gastos_hormiga: Optional[str] = None
    suscripciones: Optional[str] = None
    fondo_emergencias: Optional[str] = None
    oportunidades: Optional[str] = None
    capacidad_ahorro_adicional: Optional[str] = None
    limitaciones_analisis: Optional[str] = None
    error_parseo: Optional[str] = None
    texto_crudo: Optional[str] = None


class AnalisisIAResponse(BaseModel):
    id: UUID
    usuario_id: UUID
    tipo_analisis: str
    ciclos_analizados: int
    periodo_inicio: date
    periodo_fin: date
    perfil_detectado: Optional[dict] = None
    payload_enviado: Optional[dict] = None
    resultado: Optional[str] = None
    resultado_secciones: Optional[Union[SeccionesAnalisis, dict]] = None
    estado: str
    error_detalle: Optional[str] = None
    modelo_usado: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    costo_usd: Optional[Decimal] = None
    generado_por: str
    creado_en: datetime

    model_config = ConfigDict(from_attributes=True)


class ExportacionResponse(BaseModel):
    texto: str
    instrucciones: str
    advertencias: list[str] = Field(default_factory=list)
