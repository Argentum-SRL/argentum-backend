# -*- coding: utf-8 -*-
"""
tests/test_permitirse_evaluacion.py — Pruebas puras del bloque T1 (TE1 a TE6).
"""
from __future__ import annotations

from decimal import Decimal

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.services.permitirse_evaluacion import (
    evaluar_cuotas_cuentas,
    evaluar_mensual_cuentas,
    piso_por_contado,
    sobrante_prudente,
)
from app.services.permitirse_rubros import RECORTABLES, elegir_rubro_recortable
from app.services.permitirse_service import mes_liberacion


def test_te1_sobrante_prudente():
    assert sobrante_prudente([
        Decimal("50000"),
        Decimal("30000"),
        Decimal("80000"),
        Decimal("-10000"),
        Decimal("60000"),
    ]) == Decimal("23333.33")

    assert sobrante_prudente([
        Decimal("50000"),
        Decimal("40000"),
        Decimal("60000"),
        Decimal("55000"),
        Decimal("45000"),
        Decimal("50000"),
    ]) == Decimal("45000.00")

    assert sobrante_prudente([Decimal("10"), Decimal("20")]) is None
    assert sobrante_prudente([Decimal("100"), Decimal("100"), Decimal("100")]) == Decimal("100.00")


def test_te2_evaluar_cuotas_cuentas():
    cuotas = [Decimal("100000")] * 6

    r1 = evaluar_cuotas_cuentas(
        cuotas, [Decimal("300000")] * 6, Decimal("50000"), Decimal("0"), Decimal("0")
    )
    assert r1["resultado"] == "bien"
    assert r1["r_min"] == Decimal("200000")

    r2 = evaluar_cuotas_cuentas(
        cuotas, [Decimal("120000")] * 6, Decimal("50000"), Decimal("0"), Decimal("0")
    )
    assert r2["resultado"] == "justo"
    assert r2["r_min"] == Decimal("20000")
    assert r2["deficit"] == Decimal("0.00")
    assert r2["usa_cuentas"] is False

    r3 = evaluar_cuotas_cuentas(
        cuotas, [Decimal("0")] * 6, Decimal("50000"), Decimal("6000000"), Decimal("0")
    )
    assert r3["resultado"] == "justo"
    assert r3["deficit"] == Decimal("600000.00")
    assert r3["meses_deficit"] == 6
    assert r3["uso_mensual"] == Decimal("100000")
    assert r3["usa_cuentas"] is True

    r4 = evaluar_cuotas_cuentas(
        cuotas, [Decimal("40000")] * 6, Decimal("50000"), Decimal("300000"), Decimal("100000")
    )
    assert r4["resultado"] == "ahorros"
    assert r4["deficit"] == Decimal("360000.00")
    assert r4["falta"] == Decimal("60000.00")

    r5 = evaluar_cuotas_cuentas(
        cuotas, [Decimal("-50000")] * 6, Decimal("50000"), Decimal("100000"), Decimal("0")
    )
    assert r5["resultado"] == "no_alcanza"
    assert r5["deficit"] == Decimal("600000.00")
    assert r5["falta"] == Decimal("500000.00")

    r6 = evaluar_cuotas_cuentas(
        cuotas, [Decimal("70000")] * 6, None, Decimal("0"), Decimal("1000000")
    )
    assert r6["resultado"] == "ahorros"
    assert r6["deficit"] == Decimal("180000.00")
    assert r6["falta"] == Decimal("180000.00")


def test_te3_evaluar_mensual_cuentas():
    precio = Decimal("50000")
    colchon = Decimal("10000")

    r1 = evaluar_mensual_cuentas(
        precio, [Decimal("20000")] * 36, colchon, Decimal("600000"), Decimal("0")
    )
    assert r1["resultado"] == "justo"
    assert r1["deficit_mensual"] == Decimal("30000.00")
    assert r1["meses_cuentas"] == 20

    r2 = evaluar_mensual_cuentas(
        precio, [Decimal("0")] * 36, colchon, Decimal("300000"), Decimal("400000")
    )
    assert r2["resultado"] == "ahorros"
    assert r2["meses_cuentas"] == 6
    assert r2["meses_total"] == 14

    r3 = evaluar_mensual_cuentas(
        precio, [Decimal("-10000")] * 36, colchon, Decimal("100000"), Decimal("0")
    )
    assert r3["resultado"] == "no_alcanza"
    assert r3["deficit_mensual"] == Decimal("50000.00")
    assert r3["meses_cuentas"] == 2

    r4 = evaluar_mensual_cuentas(
        precio, [Decimal("0")] * 36, colchon, Decimal("0"), Decimal("0")
    )
    assert r4["resultado"] == "no_alcanza"
    assert r4["meses_cuentas"] == 0
    assert r4["meses_total"] == 0

    r5 = evaluar_mensual_cuentas(
        precio, [Decimal("0")] * 36, colchon, Decimal("6000000"), Decimal("0")
    )
    assert r5["resultado"] == "justo"
    assert r5["meses_cuentas"] == 120


def test_te4_piso_por_contado():
    assert piso_por_contado("no_alcanza", "bien", False) == "justo"
    assert piso_por_contado("ahorros", "justo", False) == "justo"
    assert piso_por_contado("no_alcanza", "bien", True) == "no_alcanza"
    assert piso_por_contado("ahorros", "ahorros", False) == "ahorros"
    assert piso_por_contado("bien", "justo", False) == "bien"


def test_te5_mes_liberacion_nuevo():
    colchon = Decimal("50000")
    sin_cuotas = [Decimal("0")] * 12
    assert mes_liberacion(
        [Decimal("500000")] * 12,
        [Decimal("1000000")] * 6,
        colchon,
        sin_cuotas,
    ) is None

    existentes = [Decimal("300000"), Decimal("300000")] + [Decimal("0")] * 10
    margenes = [Decimal("500000") - lib for lib in existentes]
    assert mes_liberacion(
        margenes,
        [Decimal("300000")] * 6,
        colchon,
        existentes,
    ) == 3

    # Si esta compra termina antes (2 cuotas), no rellena con ceros después de la última cuota
    assert mes_liberacion(
        margenes,
        [Decimal("300000"), Decimal("300000")],
        colchon,
        existentes,
    ) is None


def test_te6_recortables():
    subcats_egreso = {
        sub.nombre
        for cat in ARBOL_CATEGORIAS
        if cat.tipo == "egreso"
        for sub in cat.subcategorias
        if not sub.es_sistema
    }
    for nombre in RECORTABLES:
        assert nombre in subcats_egreso, f"{nombre} no está en el catálogo"

    elegido = elegir_rubro_recortable({
        "Supermercado": Decimal("300000"),
        "Delivery": Decimal("40000"),
        "Kiosco": Decimal("25000"),
        "Alquiler": Decimal("500000"),
    })
    assert elegido == "Delivery"

    assert elegir_rubro_recortable({
        "Supermercado": Decimal("300000"),
        "Farmacia": Decimal("50000"),
    }) is None
