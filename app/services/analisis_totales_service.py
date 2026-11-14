# -*- coding: utf-8 -*-
"""
app/services/analisis_totales_service.py

Cálculo de totales_terminados y total_desglose para reportes de Análisis.
Mismos campos y resultados que en la versión previa, extraído para mantener
legibilidad y respetar límites de tamaño sin condensar líneas.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Tuple


def calcular_totales_reporte(
    serie_ciclos: List[Dict[str, Any]],
    desglose: List[Dict[str, Any]],
    solo_gastos: bool,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Calcula totales_terminados y total_desglose para reportes.
    Solo considera ciclos terminados (sin datos=False y es_actual=False).
    """
    terminados = [
        c for c in serie_ciclos
        if not c.get("sin_datos") and not c.get("es_actual")
    ]
    if terminados:
        sum_g_term = sum(
            (Decimal(str(c["gastos"])) for c in terminados if c.get("gastos") is not None),
            Decimal("0"),
        )
        if solo_gastos:
            totales_terminados = {
                "ingresos": None,
                "gastos": round(float(sum_g_term), 2),
                "balance": None,
                "tasa_ahorro_pct": None,
            }
        else:
            sum_i_term = sum(
                (Decimal(str(c["ingresos"])) for c in terminados if c.get("ingresos") is not None),
                Decimal("0"),
            )
            bal_term = sum_i_term - sum_g_term
            tasa_term = (
                round(float((bal_term / sum_i_term) * Decimal("100")), 1)
                if sum_i_term > Decimal("0")
                else None
            )
            totales_terminados = {
                "ingresos": round(float(sum_i_term), 2),
                "gastos": round(float(sum_g_term), 2),
                "balance": round(float(bal_term), 2),
                "tasa_ahorro_pct": tasa_term,
            }
    else:
        totales_terminados = {
            "ingresos": None if solo_gastos else 0.0,
            "gastos": 0.0,
            "balance": None if solo_gastos else 0.0,
            "tasa_ahorro_pct": None,
        }

    tot_monto_desglose = sum(
        (Decimal(str(cat["monto"])) for cat in desglose),
        Decimal("0"),
    )
    total_desglose = {
        "monto": round(float(tot_monto_desglose), 2),
        "cantidad_movimientos": sum(
            cat.get("cantidad_movimientos", 0) for cat in desglose
        ),
    }
    return totales_terminados, total_desglose
