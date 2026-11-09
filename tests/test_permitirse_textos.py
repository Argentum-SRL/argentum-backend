# -*- coding: utf-8 -*-
"""
tests/test_permitirse_textos.py — Pruebas unitarias de textos_permitirse y límites de caracteres.
"""
from datetime import date
from decimal import Decimal

from app.services.permitirse_textos import textos_permitirse
from app.services.tools_service import PlanCuotas


def test_contado_justo_con_historia():
    # contado justo veces 1 n 3 fin 30/11 → "En 1 de tus últimos 3 meses no te habría alcanzado hasta el 30/11."
    res = textos_permitirse({
        "modo": "contado",
        "resultado": "justo",
        "veces": 1,
        "n": 3,
        "fin_ciclo": date(2026, 11, 30),
    })
    assert res["linea_principal"] == "En 1 de tus \u00faltimos 3 meses no te habr\u00eda alcanzado hasta el 30/11."


def test_contado_sin_historia():
    # contado sin historia, libre 300.000, precio 1.000 → "Hoy te quedarían $299.000 libres, pero todavía no conozco bien tus meses."
    res = textos_permitirse({
        "modo": "contado",
        "resultado": "justo",
        "veces": 0,
        "n": 0,
        "libre": Decimal("300000"),
        "precio": Decimal("1000"),
        "fin_ciclo": date(2026, 11, 30),
    })
    assert res["linea_principal"] == "Hoy te quedar\u00edan $299.000 libres, pero todav\u00eda no conozco bien tus meses."


def test_mensual_ahorros_meses():
    # mensual ahorros d 30.000 meses 6 → "Te faltarían $30.000 por mes: tus ahorros alcanzan para 6 meses."
    res6 = textos_permitirse({
        "modo": "mensual",
        "resultado": "ahorros",
        "deficit_mensual": Decimal("30000"),
        "meses_alcanzan": 6,
    })
    assert res6["linea_principal"] == "Te faltar\u00edan $30.000 por mes: tus ahorros alcanzan para 6 meses."

    # meses 1 → "... alcanzan para 1 mes."
    res1 = textos_permitirse({
        "modo": "mensual",
        "resultado": "ahorros",
        "deficit_mensual": Decimal("30000"),
        "meses_alcanzan": 1,
    })
    assert res1["linea_principal"] == "Te faltar\u00edan $30.000 por mes: tus ahorros alcanzan para 1 mes."


def test_cuotas_ahorros_k_meses():
    # cuotas ahorros total 40.000 k 2 → "Te faltarían $40.000 en 2 meses: tendrías que sacarlos de tus ahorros."
    res2 = textos_permitirse({
        "modo": "cuotas",
        "resultado": "ahorros",
        "deficit_total": Decimal("40000"),
        "meses_deficit": 2,
    })
    assert res2["linea_principal"] == "Te faltar\u00edan $40.000 en 2 meses: tendr\u00edas que sacarlos de tus ahorros."

    res1 = textos_permitirse({
        "modo": "cuotas",
        "resultado": "ahorros",
        "deficit_total": Decimal("40000"),
        "meses_deficit": 1,
    })
    assert res1["linea_principal"] == "Te faltar\u00edan $40.000 en 1 mes: tendr\u00edas que sacarlos de tus ahorros."


def test_linea_liberacion():
    # liberación "marzo" → "Desde marzo te entra bien: terminás de pagar otras cuotas."
    res = textos_permitirse({
        "mes_liberacion_nombre": "marzo",
    })
    assert res["linea_liberacion"] == "Desde marzo te entra bien: termin\u00e1s de pagar otras cuotas."


def test_linea_compensar():
    # compensar contado 10.000 "Alimentación" → "Si este mes gastás $10.000 menos en Alimentación, te entra bien."
    res_c = textos_permitirse({
        "modo": "contado",
        "rubro_compensar": "Alimentaci\u00f3n",
        "monto_compensar": Decimal("10000"),
    })
    assert res_c["linea_compensar"] == "Si este mes gast\u00e1s $10.000 menos en Alimentaci\u00f3n, te entra bien."

    # mensual 5.000 "Ocio" → "Si gastás $5.000 menos por mes en Ocio, te entra bien."
    res_m = textos_permitirse({
        "modo": "mensual",
        "rubro_compensar": "Ocio",
        "monto_compensar": Decimal("5000"),
    })
    assert res_m["linea_compensar"] == "Si gast\u00e1s $5.000 menos por mes en Ocio, te entra bien."


def test_lineas_plan():
    # plan 6 cuotas tna 60 de 300.000 → lineas_plan[0] "Son 6 cuotas de $59.105 ($354.631 en total).", lineas_plan[1] "Pagás $54.631 de interés."
    plan6 = PlanCuotas(
        monto_cuota=Decimal("59105"),
        total=Decimal("354631"),
        interes=Decimal("54631"),
        valor_presente=None,
    )
    res6 = textos_permitirse({
        "modo": "cuotas",
        "cantidad_cuotas": 6,
        "plan": plan6,
    })
    assert res6["lineas_plan"][0] == "Son 6 cuotas de $59.105 ($354.631 en total)."
    assert res6["lineas_plan"][1] == "Pag\u00e1s $54.631 de inter\u00e9s."

    # plan 12 × 10.000 con vp 105.753 y fuente "REM de septiembre"
    # → lineas_plan ["Son 12 cuotas de $10.000.", "En plata de hoy equivalen a $105.753 (REM de septiembre)."]
    plan12 = PlanCuotas(
        monto_cuota=Decimal("10000"),
        total=Decimal("120000"),
        interes=Decimal("0"),
        valor_presente=Decimal("105753"),
    )
    res12 = textos_permitirse({
        "modo": "cuotas",
        "cantidad_cuotas": 12,
        "plan": plan12,
        "fuente_inflacion": "REM de septiembre",
    })
    assert res12["lineas_plan"] == [
        "Son 12 cuotas de $10.000.",
        "En plata de hoy equivalen a $105.753 (REM de septiembre).",
    ]


def test_linea_frescura_datos_viejos():
    # datos viejos 13 días → "Ojo: hace 13 días que no cargás nada, puede no estar al día."
    res = textos_permitirse({
        "motivo": "datos_viejos",
        "dias_sin_cargar": 13,
    })
    assert res["linea_frescura"] == "Ojo: hace 13 d\u00edas que no carg\u00e1s nada, puede no estar al d\u00eda."


def test_linea_ahorros_no_en_justo_mut14():
    # Para capturar mutación 14: linea_ahorros no debe estar presente en "justo" aunque ahorros_total > 0
    res_justo = textos_permitirse({
        "modo": "contado",
        "resultado": "justo",
        "ahorros_total": Decimal("500000"),
    })
    assert res_justo["linea_ahorros"] is None

    res_ahorros = textos_permitirse({
        "modo": "contado",
        "resultado": "ahorros",
        "ahorros_total": Decimal("500000"),
    })
    assert res_ahorros["linea_ahorros"] == "Tus ahorros suman $500.000."


def test_longitud_maxima_90_caracteres():
    # Con montos de −999.999.999, 120 cuotas, mes "septiembre" y rubro "Cuidado personal": cada línea tiene 90 caracteres o menos.
    plan120 = PlanCuotas(
        monto_cuota=Decimal("-999999999"),
        total=Decimal("-999999999"),
        interes=Decimal("-999999999"),
        valor_presente=Decimal("-999999999"),
    )
    res = textos_permitirse({
        "modo": "cuotas",
        "resultado": "ahorros",
        "motivo": "datos_viejos",
        "precio": Decimal("-999999999"),
        "libre": Decimal("-999999999"),
        "fin_ciclo": date(2026, 9, 30),
        "dias_sin_cargar": 120,
        "ahorros_total": Decimal("-999999999"),
        "veces": 120,
        "n": 120,
        "falta": Decimal("-999999999"),
        "min_finales": Decimal("-999999999"),
        "max_finales": Decimal("-999999999"),
        "r_min": Decimal("-999999999"),
        "deficit_total": Decimal("-999999999"),
        "meses_deficit": 120,
        "deficit_mensual": Decimal("-999999999"),
        "meses_alcanzan": 120,
        "plan": plan120,
        "cantidad_cuotas": 120,
        "fuente_inflacion": "REM de septiembre",
        "mes_liberacion_nombre": "septiembre",
        "rubro_compensar": "Cuidado personal",
        "monto_compensar": Decimal("-999999999"),
    })

    assert len(res["titulo"]) <= 90
    assert len(res["linea_principal"]) <= 90
    for lp in res["lineas_plan"]:
        assert len(lp) <= 90, f"Línea de plan supera 90 caracteres ({len(lp)}): {lp}"
    if res["linea_ahorros"]:
        assert len(res["linea_ahorros"]) <= 90
    if res["linea_liberacion"]:
        assert len(res["linea_liberacion"]) <= 90
    if res["linea_compensar"]:
        assert len(res["linea_compensar"]) <= 90
    if res["linea_frescura"]:
        assert len(res["linea_frescura"]) <= 90
