"""
app/schemas/fin_de_mes.py — Esquemas de respuesta para la ruta GET /dashboard/fin-de-mes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.schemas.tipos import DecimalJSON


class FinDeMesTextos(BaseModel):
    titulo: str
    monto: str
    subtitulo: str
    linea_rango: Optional[str] = None
    linea_riesgo: Optional[str] = None
    linea_estado: Optional[str] = None
    linea_cobertura: Optional[str] = None
    linea_saldo: Optional[str] = None


class FinDeMesFila(BaseModel):
    fecha: str
    nombre: str
    monto: str


class FinDeMesMoneda(BaseModel):
    textos: FinDeMesTextos
    calendario: List[FinDeMesFila] = Field(default_factory=list)


class FinDeMesResponse(BaseModel):
    estado: str
    fecha_limite: str
    proximo_ingreso: Optional[str] = None
    hasta_ingreso: bool
    libre_ars: DecimalJSON
    libre_usd: DecimalJSON
    rango_bajo: Optional[DecimalJSON] = None
    rango_alto: Optional[DecimalJSON] = None
    ciclos_historia: int
    veces_no_alcanza: Optional[int] = None
    cobertura: Optional[int] = None
    saldo_inicio: DecimalJSON
    movimiento: DecimalJSON
    saldo_hoy: DecimalJSON
    contrato: Dict[str, Any]
    mostrar_modal_bienvenida: bool
    ars: FinDeMesMoneda
    usd: FinDeMesMoneda

