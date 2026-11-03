"""
app/schemas/fin_de_mes.py — Esquemas de respuesta para la ruta GET /dashboard/fin-de-mes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from pydantic import BaseModel
from app.schemas.tipos import DecimalJSON


class FinDeMesTarjeta(BaseModel):
    titulo: str
    monto: str
    subtitulo: str
    linea_riesgo: Optional[str] = None
    linea_cobro: Optional[str] = None
    linea_cobertura: Optional[str] = None


class FinDeMesResponse(BaseModel):
    estado: str
    mostrar_tarjeta: bool
    fin_ciclo: str
    proximo_ingreso: Optional[str] = None
    cobra_en_ciclo: bool
    libre_ars: DecimalJSON
    libre_hasta_cobro: Optional[DecimalJSON] = None
    ingreso_esperado: DecimalJSON
    libre_usd: DecimalJSON
    rango_bajo: Optional[DecimalJSON] = None
    rango_alto: Optional[DecimalJSON] = None
    ciclos_historia: int
    veces_no_alcanza: Optional[int] = None
    cobertura: Optional[int] = None
    saldo_inicio: DecimalJSON
    movimiento: DecimalJSON
    saldo_hoy: DecimalJSON
    linea_saldo: str
    tarjeta: Optional[FinDeMesTarjeta] = None
    contrato: Dict[str, Any]
    mostrar_modal_bienvenida: bool
