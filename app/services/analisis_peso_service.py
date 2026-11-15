# -*- coding: utf-8 -*-
"""
app/services/analisis_peso_service.py
Cálculo del gasto total de referencia y peso porcentual sobre el total
para vistas de Análisis con filtros de gasto (categoría, tarjeta, naturaleza).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.usuario import Usuario
from app.services.analisis_ciclos_service import (
    calcular_kpis_totales,
    calcular_promedios_y_tasa,
    calcular_serie_ciclos,
)


def calcular_gasto_referencia(
    db: Session,
    usuario: Usuario,
    ciclos: List[Dict[str, Any]],
    fecha_inicio_global: date,
    fecha_fin_global: date,
    ref_hoy: date,
    modo: str,
    billetera_id: Optional[UUID],
    gastos_filtrados: float,
    fechas_con_tx: Optional[set[date]],
    consultar_ventana_fn: Any,
) -> tuple[Optional[float], Optional[float]]:
    """Calcula (gasto_total_referencia, peso_sobre_gasto_total_pct)

    usando las mismas funciones que arman gastos_totales, pero sin
    los filtros de categoría, tarjeta ni naturaleza.
    """
    rows_g_ref, _, cots_map_ref, _ = consultar_ventana_fn(
        db, usuario, fecha_inicio_global, fecha_fin_global, ref_hoy,
        modo, billetera_id, None, None, None, True,
    )
    serie_raw_ref, _, _ = calcular_serie_ciclos(
        ciclos, ref_hoy, modo, True, rows_g_ref, [], cots_map_ref, fechas_con_tx,
    )
    _, _, _, _, serie_ciclos_ref = calcular_promedios_y_tasa(
        serie_raw_ref, ref_hoy, True,
    )
    _, suma_gastos_ref, _, _ = calcular_kpis_totales(
        serie_ciclos_ref, ref_hoy, True,
    )
    ref = round(float(suma_gastos_ref), 2)
    peso_pct: Optional[float] = None
    if ref > 0:
        peso_pct = round((gastos_filtrados / ref) * 100.0, 1)
    return ref, peso_pct


def resolver_textos_auxiliares(
    modo: str,
    movs_sin_cot: int,
    solo_gastos: bool,
    cant_ciclos: int,
    tasa_ventana: Optional[float],
    es_ult_en_curso: bool,
) -> tuple[Optional[str], Optional[str]]:
    """Genera aviso_cotizacion y tasa_ahorro_texto para el resumen."""
    aviso: Optional[str] = None
    if movs_sin_cot > 0:
        if modo == "consolidado_ars":
            if movs_sin_cot == 1:
                aviso = (
                    "1 movimiento en dólares no se pudo pasar a pesos "
                    "porque falta la cotización de ese día."
                )
            else:
                aviso = (
                    f"{movs_sin_cot} movimientos en dólares no se pudieron "
                    "pasar a pesos porque falta la cotización de esos días."
                )
        elif modo == "consolidado_usd":
            if movs_sin_cot == 1:
                aviso = (
                    "1 movimiento en pesos no se pudo pasar a dólares "
                    "porque falta la cotización de ese día."
                )
            else:
                aviso = (
                    f"{movs_sin_cot} movimientos en pesos no se pudieron "
                    "pasar a dólares porque falta la cotización de esos días."
                )

    tasa_txt: Optional[str] = None
    if not solo_gastos:
        if cant_ciclos == 1:
            tasa_txt = "Aparece cuando termine el ciclo."
        elif tasa_ventana is not None and es_ult_en_curso:
            tasa_txt = "Sin contar el ciclo en curso."
    return aviso, tasa_txt

