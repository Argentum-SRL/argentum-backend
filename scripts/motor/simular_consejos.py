# -*- coding: utf-8 -*-
"""Simulación de consejos con personas sintéticas."""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

# Asegurar raíz del backend en sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.services.consejos_reglas import (
    FilaGasto,
    evaluar_hormiga,
    evaluar_comisiones,
    evaluar_pico,
    evaluar_sin_rubro,
    evaluar_sin_ingresos,
    evaluar_sin_presupuesto,
    elegir_rubro_sin_presupuesto,
    tickets_90,
    es_compra,
    a_precios_de_hoy,
    fuera_de_consejos,
)
from app.services.definiciones_service import ContextoDefiniciones, es_gasto, es_ingreso
from app.utils.finanzas import mediana
from tests.motor.personas import generar_todas_las_personas
from tests.motor.personas.base_tiempo import FECHA_FIN_HISTORIA
from tests.motor.personas.catalogo import IPC_MAP


def simular_para_persona(persona: Any, hoy: date = FECHA_FIN_HISTORIA) -> dict[str, Any]:
    """Evalúa C1, C4, C5, C14 y C15 para una persona sintética."""
    ctx = ContextoDefiniciones(
        grupos_cuotas_cantidades={},
        billeteras_inversion_ids=set(),
        categoria_ahorro_ids=set(),
        subcategoria_tarjeta_id=None,
        subcategoria_tarjeta_ids=set(),
        hoy=hoy,
    )

    # Armar filas de gasto con es_gasto
    filas: list[FilaGasto] = []
    for tx in persona.movimientos:
        if es_gasto(tx, ctx):
            cat_nom = tx.categoria.nombre if getattr(tx, "categoria", None) else None
            sub_nom = tx.subcategoria.nombre if getattr(tx, "subcategoria", None) else None
            mon = tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda)
            es_esp = getattr(tx, "reintegro_de_id", None) is not None
            es_cuo = bool(getattr(tx, "es_cuota_hija", False))
            filas.append(
                FilaGasto(
                    fecha=tx.fecha,
                    monto=Decimal(str(tx.monto)),
                    moneda=mon,
                    categoria=cat_nom,
                    subcategoria=sub_nom,
                    es_espejo=es_esp,
                    es_cuota=es_cuo,
                )
            )

    # Identificar ciclos: ciclo_actual es el que contiene 'hoy', cerrados son los anteriores ordenados más nuevo primero
    ciclos = sorted(persona.ciclos, key=lambda c: c[0])
    ciclo_actual = None
    cerrados = []
    for c in reversed(ciclos):
        if c[0] <= hoy <= c[1]:
            ciclo_actual = c
        elif c[1] < hoy:
            cerrados.append(c)

    if ciclo_actual is None and ciclos:
        ciclo_actual = ciclos[-1]
        cerrados = list(reversed(ciclos[:-1]))

    moneda_principal = getattr(persona, "moneda", "ARS")
    if hasattr(moneda_principal, "value"):
        moneda_principal = moneda_principal.value
    else:
        moneda_principal = str(moneda_principal)

    monedas = [moneda_principal]
    # Si hay movimientos en otra moneda, evaluarla también
    monedas_presentes = {f.moneda for f in filas}
    for m in monedas_presentes:
        if m not in monedas:
            monedas.append(m)

    resultados: dict[str, Any] = {
        "persona": persona.id,
        "moneda_principal": moneda_principal,
        "consejos": [],
    }

    # Filas a precios de hoy para C5
    filas_hoy = a_precios_de_hoy(filas, IPC_MAP, hoy)

    # Evaluar por cada moneda
    for mon in monedas:
        # C1: hormiga en el último cerrado
        if cerrados:
            c0 = cerrados[0]
            filas_c0 = [f for f in filas if c0[0] <= f.fecha <= c0[1] and f.moneda == mon]
            t90 = tickets_90(filas, mon, hoy)
            c1 = evaluar_hormiga(filas_c0, t90, mon, c0[0])
            if c1:
                resultados["consejos"].append(
                    {"tipo": c1.tipo, "moneda": c1.moneda, "texto": c1.texto, "valores": c1.valores}
                )

        # C4: comisiones (solo ARS)
        if mon == "ARS" and cerrados:
            filas_3 = [[f for f in filas if c[0] <= f.fecha <= c[1] and f.moneda == "ARS"] for c in cerrados[:3]]
            c4 = evaluar_comisiones(filas_3, hoy)
            if c4:
                resultados["consejos"].append(
                    {"tipo": c4.tipo, "moneda": c4.moneda, "texto": c4.texto, "valores": c4.valores}
                )

        # C5: pico (a precios de hoy)
        if cerrados and ciclo_actual:
            filas_act = [f for f in filas_hoy if ciclo_actual[0] <= f.fecha <= hoy and f.moneda == mon]
            filas_cerr_all = [[f for f in filas_hoy if c[0] <= f.fecha <= c[1] and f.moneda == mon] for c in cerrados]
            gastos_cerr = [sum(f.monto for f in fc) for fc in filas_cerr_all]
            gasto_tipico = mediana(gastos_cerr) if gastos_cerr else None
            c5 = evaluar_pico(filas_act, filas_cerr_all, gasto_tipico, mon, ciclo_actual[0])
            if c5:
                cat_pico = c5.valores["categoria"]
                totales_cerr = [
                    sum(
                        (f.monto for f in fc if f.moneda == mon and f.categoria == cat_pico and not fuera_de_consejos(f)),
                        Decimal("0"),
                    )
                    for fc in filas_cerr_all
                ]
                total_act = sum(
                    (f.monto for f in filas_act if f.moneda == mon and f.categoria == cat_pico and not fuera_de_consejos(f)),
                    Decimal("0"),
                )
                resultados["consejos"].append(
                    {
                        "tipo": c5.tipo,
                        "moneda": c5.moneda,
                        "texto": c5.texto,
                        "valores": c5.valores,
                        "totales_cerrados": [str(t) for t in totales_cerr],
                        "total_actual": str(total_act),
                    }
                )

        # C14: sin rubro
        if cerrados:
            c0 = cerrados[0]
            filas_c0 = [f for f in filas if c0[0] <= f.fecha <= c0[1] and f.moneda == mon]
            c14 = evaluar_sin_rubro(filas_c0, mon, c0[0])
            if c14:
                resultados["consejos"].append(
                    {"tipo": c14.tipo, "moneda": c14.moneda, "texto": c14.texto, "valores": c14.valores}
                )

    # C15: sin ingresos
    egresos_45 = len([f for f in filas if es_compra(f) and hoy - timedelta(days=45) < f.fecha <= hoy])
    ingresos_45 = len([tx for tx in persona.movimientos if es_ingreso(tx, ctx) and hoy - timedelta(days=45) < tx.fecha <= hoy])
    c15 = evaluar_sin_ingresos(egresos_45, ingresos_45, hoy)
    if c15:
        resultados["consejos"].append(
            {"tipo": c15.tipo, "moneda": c15.moneda, "texto": c15.texto, "valores": c15.valores}
        )

    # C7: sin presupuesto
    if len(cerrados) >= 3:
        for mon_c7 in ("ARS", "USD"):
            rubro_c7 = elegir_rubro_sin_presupuesto(
                filas_hoy,
                cerrados[:3],
                frozenset(),
                frozenset(),
                mon_c7,
            )
            if rubro_c7:
                c7 = evaluar_sin_presupuesto(rubro_c7, f"cat-{rubro_c7.categoria.lower()}", hoy)
                resultados["consejos"].append(
                    {"tipo": c7.tipo, "moneda": c7.moneda, "texto": c7.texto, "valores": c7.valores}
                )

    return resultados


def simular_todas_las_personas(hoy: date = FECHA_FIN_HISTORIA) -> list[dict[str, Any]]:
    """Simula consejos para las 10 personas sintéticas."""
    personas = generar_todas_las_personas()
    res = []
    for p in personas:
        r = simular_para_persona(p, hoy)
        res.append(r)
    return res


if __name__ == "__main__":
    resultados = simular_todas_las_personas()
    for r in resultados:
        print(f"=== Persona: {r['persona']} ({r['moneda_principal']}) ===")
        if not r["consejos"]:
            print("  (Sin consejos emitibles para C1, C4, C5, C14, C15)")
        for c in r["consejos"]:
            print(f"  Tipo: {c['tipo']} [{c['moneda']}]")
            print(f"  Texto: {c['texto']}")
            print(f"  Valores: {c['valores']}")
            if c["tipo"] == "pico":
                print(f"    Totales por cerrado (ajustados): {c.get('totales_cerrados')}")
                print(f"    Total ciclo en curso: {c.get('total_actual')}")
        print()
