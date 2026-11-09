# -*- coding: utf-8 -*-
"""
tests/test_presupuesto_sugerencias_reglas.py
Tests unitarios puros para las reglas de presupuesto:
- proyectar_ritmo y linea_ritmo
- sugerir_limite
- elegir_rubro_sin_presupuesto
"""
from datetime import date
from decimal import Decimal

from app.services.consejos_reglas import (
    FilaGasto,
    RubroSinPresupuesto,
    elegir_rubro_sin_presupuesto,
)
from app.services.presupuesto_sugerencias_reglas import (
    SugerenciaLimite,
    linea_ritmo,
    proyectar_ritmo,
    sugerir_limite,
)

I = date(2026, 11, 1)
F = date(2026, 11, 30)


def test_proyectar_ritmo_y_linea():
    # (1) gastado 310.000, fijo 300.000, pendiente 0, hoy 10/11 -> None
    res = proyectar_ritmo(Decimal("310000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res is None

    # (2) 334.000, 300.000, 0, hoy 10/11 -> 402.000 y linea "A este ritmo llegás a unos $402.000 (límite $400.000)."
    res = proyectar_ritmo(Decimal("334000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res == Decimal("402000")
    assert linea_ritmo(res, Decimal("400000"), "ARS") == "A este ritmo llegás a unos $402.000 (límite $400.000)."

    # (3) los de (2) con hoy 09/11 -> linea "A este ritmo llegás a unos $413.000 (límite $400.000)."; con hoy 08/11 -> None
    res_09 = proyectar_ritmo(Decimal("334000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 9), Decimal("400000"))
    assert res_09 is not None
    assert linea_ritmo(res_09, Decimal("400000"), "ARS") == "A este ritmo llegás a unos $413.000 (límite $400.000)."
    res_08 = proyectar_ritmo(Decimal("334000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 8), Decimal("400000"))
    assert res_08 is None

    # (4) 333.400, 300.000, 0, hoy 10/11 -> None; 333.500 -> linea "A este ritmo llegás a unos $401.000 (límite $400.000)."
    res_333400 = proyectar_ritmo(Decimal("333400"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res_333400 is None
    res_333500 = proyectar_ritmo(Decimal("333500"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res_333500 is not None
    assert linea_ritmo(res_333500, Decimal("400000"), "ARS") == "A este ritmo llegás a unos $401.000 (límite $400.000)."

    # (5) 310.000, 300.000, pendiente 80.000, hoy 10/11 -> linea "A este ritmo llegás a unos $410.000 (límite $400.000)."
    res_pend = proyectar_ritmo(Decimal("310000"), Decimal("300000"), Decimal("80000"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res_pend == Decimal("410000")
    assert linea_ritmo(res_pend, Decimal("400000"), "ARS") == "A este ritmo llegás a unos $410.000 (límite $400.000)."

    # (6) 400.001, 0, 0, hoy 10/11 -> None; 400.000, 0, 0 -> linea "A este ritmo llegás a unos $1.200.000 (límite $400.000)."
    res_over = proyectar_ritmo(Decimal("400001"), Decimal("0"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res_over is None
    res_exact = proyectar_ritmo(Decimal("400000"), Decimal("0"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res_exact == Decimal("1200000")
    assert linea_ritmo(res_exact, Decimal("400000"), "ARS") == "A este ritmo llegás a unos $1.200.000 (límite $400.000)."

    # (7) 290.000, 300.000, 0 -> None
    res_neg = proyectar_ritmo(Decimal("290000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400000"))
    assert res_neg is None

    # (8) hoy 31/10 -> None; hoy 01/12 -> None; límite 0 -> None
    assert proyectar_ritmo(Decimal("334000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 10, 31), Decimal("400000")) is None
    assert proyectar_ritmo(Decimal("334000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 12, 1), Decimal("400000")) is None
    assert proyectar_ritmo(Decimal("334000"), Decimal("300000"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("0")) is None

    # (9) USD: 334, 300, 0, límite 400, hoy 10/11 -> "A este ritmo llegás a unos US$402 (límite US$400)."
    res_usd = proyectar_ritmo(Decimal("334"), Decimal("300"), Decimal("0"), I, F, date(2026, 11, 10), Decimal("400"), "USD")
    assert res_usd == Decimal("402")
    assert linea_ritmo(res_usd, Decimal("400"), "USD") == "A este ritmo llegás a unos US$402 (límite US$400)."

    # (10) quincenal: período 16/11 a 30/11, hoy 20/11, 100.000, 0, 0, límite 250.000 -> "A este ritmo llegás a unos $300.000 (límite $250.000)."
    i_q = date(2026, 11, 16)
    f_q = date(2026, 11, 30)
    res_q = proyectar_ritmo(Decimal("100000"), Decimal("0"), Decimal("0"), i_q, f_q, date(2026, 11, 20), Decimal("250000"))
    assert res_q == Decimal("300000")
    assert linea_ritmo(res_q, Decimal("250000"), "ARS") == "A este ritmo llegás a unos $300.000 (límite $250.000)."


def test_sugerir_limite():
    # (a) mensual, previos [(52.300, 50.000, 6), (60.000, 50.000, 8), (51.000, 50.000, 5)], límite actual 50.000, calculado actual 20.000, compras 3
    # -> texto "Te pasaste los últimos 3 meses. Lo normal tuyo es $53.000.", boton "Subir a $53.000", monto 53.000
    previos_a = [
        (Decimal("52300"), Decimal("50000"), 6),
        (Decimal("60000"), Decimal("50000"), 8),
        (Decimal("51000"), Decimal("50000"), 5),
    ]
    sug_a = sugerir_limite("mensual", Decimal("50000"), Decimal("20000"), 3, previos_a, "ARS")
    assert sug_a == SugerenciaLimite(
        texto="Te pasaste los últimos 3 meses. Lo normal tuyo es $53.000.",
        boton="Subir a $53.000",
        monto=Decimal("53000"),
    )

    # (b) con el segundo previo en (50.000, 50.000, 6) -> None
    previos_b = [
        (Decimal("52300"), Decimal("50000"), 6),
        (Decimal("50000"), Decimal("50000"), 6),
        (Decimal("51000"), Decimal("50000"), 5),
    ]
    assert sugerir_limite("mensual", Decimal("50000"), Decimal("20000"), 3, previos_b, "ARS") is None

    # (c) los de (a) con límite actual 53.000 -> None; con 52.999 -> el texto de (a)
    assert sugerir_limite("mensual", Decimal("53000"), Decimal("20000"), 3, previos_a, "ARS") is None
    sug_c = sugerir_limite("mensual", Decimal("52999"), Decimal("20000"), 3, previos_a, "ARS")
    assert sug_c is not None
    assert sug_c.texto == "Te pasaste los últimos 3 meses. Lo normal tuyo es $53.000."

    # (d) solo los dos primeros previos -> None
    assert sugerir_limite("mensual", Decimal("50000"), Decimal("20000"), 3, previos_a[:2], "ARS") is None

    # (e) los de (a) en quincenal -> "Te pasaste las últimas 3 quincenas. Lo normal tuyo es $53.000."; en semanal -> "Te pasaste las últimas 3 semanas. Lo normal tuyo es $53.000."
    sug_e_q = sugerir_limite("quincenal", Decimal("50000"), Decimal("20000"), 3, previos_a, "ARS")
    assert sug_e_q is not None
    assert sug_e_q.texto == "Te pasaste las últimas 3 quincenas. Lo normal tuyo es $53.000."
    assert sug_e_q.boton == "Subir a $53.000"

    sug_e_s = sugerir_limite("semanal", Decimal("50000"), Decimal("20000"), 3, previos_a, "ARS")
    assert sug_e_s is not None
    assert sug_e_s.texto == "Te pasaste las últimas 3 semanas. Lo normal tuyo es $53.000."
    assert sug_e_s.boton == "Subir a $53.000"

    # (f) escalón: mensual, previos 3 × (80.000, 85.000, 1), límite 85.000, actual 92.000 con 1 compra
    # -> "Este mes el pago subió a $92.000, más que tu límite de $85.000.", "Subir a $92.000", 92.000;
    # con actual 92.500 -> "Este mes el pago subió a $92.500, más que tu límite de $85.000.", "Subir a $93.000", 93.000.
    previos_f = [
        (Decimal("80000"), Decimal("85000"), 1),
        (Decimal("80000"), Decimal("85000"), 1),
        (Decimal("80000"), Decimal("85000"), 1),
    ]
    sug_f = sugerir_limite("mensual", Decimal("85000"), Decimal("92000"), 1, previos_f, "ARS")
    assert sug_f == SugerenciaLimite(
        texto="Este mes el pago subió a $92.000, más que tu límite de $85.000.",
        boton="Subir a $92.000",
        monto=Decimal("92000"),
    )
    sug_f_round = sugerir_limite("mensual", Decimal("85000"), Decimal("92500"), 1, previos_f, "ARS")
    assert sug_f_round == SugerenciaLimite(
        texto="Este mes el pago subió a $92.500, más que tu límite de $85.000.",
        boton="Subir a $93.000",
        monto=Decimal("93000"),
    )

    # (g) previos 3 × (80.000, 85.000, 2) y actual 92.000 con 1 compra -> None
    previos_g = [
        (Decimal("80000"), Decimal("85000"), 2),
        (Decimal("80000"), Decimal("85000"), 2),
        (Decimal("80000"), Decimal("85000"), 2),
    ]
    assert sugerir_limite("mensual", Decimal("85000"), Decimal("92000"), 1, previos_g, "ARS") is None

    # (h) los de (f) en quincenal -> None
    assert sugerir_limite("quincenal", Decimal("85000"), Decimal("92000"), 1, previos_f, "ARS") is None

    # (i) los de (f) con actual 85.000 -> None
    assert sugerir_limite("mensual", Decimal("85000"), Decimal("85000"), 1, previos_f, "ARS") is None

    # (j) USD: previos 3 × (80, 85, 1), actual 92 con 1 compra
    # -> "Este mes el pago subió a US$92, más que tu límite de US$85.", "Subir a US$92"
    previos_j = [
        (Decimal("80"), Decimal("85"), 1),
        (Decimal("80"), Decimal("85"), 1),
        (Decimal("80"), Decimal("85"), 1),
    ]
    sug_j = sugerir_limite("mensual", Decimal("85"), Decimal("92"), 1, previos_j, "USD")
    assert sug_j == SugerenciaLimite(
        texto="Este mes el pago subió a US$92, más que tu límite de US$85.",
        boton="Subir a US$92",
        monto=Decimal("92"),
    )

    # (k) escalón gana: previos 3 × (90.000, 85.000, 1), límite 85.000, actual 95.000 con 1 compra
    # -> "Este mes el pago subió a $95.000, más que tu límite de $85.000.", monto 95.000
    previos_k = [
        (Decimal("90000"), Decimal("85000"), 1),
        (Decimal("90000"), Decimal("85000"), 1),
        (Decimal("90000"), Decimal("85000"), 1),
    ]
    sug_k = sugerir_limite("mensual", Decimal("85000"), Decimal("95000"), 1, previos_k, "ARS")
    assert sug_k is not None
    assert sug_k.texto == "Este mes el pago subió a $95.000, más que tu límite de $85.000."
    assert sug_k.monto == Decimal("95000")


def test_elegir_rubro_sin_presupuesto():
    cerrados3 = [
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 8, 1), date(2026, 8, 31)),
    ]

    def fila(d, monto, cat, sub, moneda="ARS", es_cuota=False, es_espejo=False):
        return FilaGasto(
            fecha=d,
            monto=Decimal(str(monto)),
            moneda=moneda,
            categoria=cat,
            subcategoria=sub,
            es_cuota=es_cuota,
            es_espejo=es_espejo,
        )

    # Base: por ciclo Alimentación > Supermercado 950.000 y Gastronomía > Restaurantes 2 x 25.000
    base_filas = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        base_filas.append(fila(d, 950000, "Alimentación", "Supermercado"))
        base_filas.append(fila(d, 25000, "Gastronomía", "Restaurantes"))
        base_filas.append(fila(d, 25000, "Gastronomía", "Restaurantes"))

    res = elegir_rubro_sin_presupuesto(base_filas, cerrados3, set(), set(), "ARS")
    assert res == RubroSinPresupuesto("Gastronomía", "ARS", Decimal("50000"), Decimal("50000"))

    # base con Supermercado 950.001 -> None (50.000 * 100 < 1.000.001 * 5 = 5.000.005)
    filas_950001 = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        filas_950001.append(fila(d, 950001, "Alimentación", "Supermercado"))
        filas_950001.append(fila(d, 25000, "Gastronomía", "Restaurantes"))
        filas_950001.append(fila(d, 25000, "Gastronomía", "Restaurantes"))
    assert elegir_rubro_sin_presupuesto(filas_950001, cerrados3, set(), set(), "ARS") is None

    # Gastronomía 50.400 / 60.000 / 48.000 (una compra por ciclo, en ese orden de octubre a agosto) con Supermercado 900.000 -> monto 51.000
    filas_gastro_mediana = [
        fila(date(2026, 10, 10), 900000, "Alimentación", "Supermercado"),
        fila(date(2026, 10, 10), 50400, "Gastronomía", "Restaurantes"),
        fila(date(2026, 9, 10), 900000, "Alimentación", "Supermercado"),
        fila(date(2026, 9, 10), 60000, "Gastronomía", "Restaurantes"),
        fila(date(2026, 8, 10), 900000, "Alimentación", "Supermercado"),
        fila(date(2026, 8, 10), 48000, "Gastronomía", "Restaurantes"),
    ]
    res_med = elegir_rubro_sin_presupuesto(filas_gastro_mediana, cerrados3, set(), set(), "ARS")
    assert res_med is not None
    assert res_med.categoria == "Gastronomía"
    assert res_med.monto == Decimal("51000")
    assert res_med.mediana == Decimal("50400")

    # Supermercado 500.000 + Gastronomía 2 x 30.000 + Ocio > Salidas 40.000 por ciclo -> Gastronomía 60.000;
    # con cubiertas {"Gastronomía"} -> Ocio 40.000;
    # con nombres {"gastronomía"} -> Ocio 40.000
    filas_dos_rubros = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        filas_dos_rubros.append(fila(d, 500000, "Alimentación", "Supermercado"))
        filas_dos_rubros.append(fila(d, 30000, "Gastronomía", "Restaurantes"))
        filas_dos_rubros.append(fila(d, 30000, "Gastronomía", "Restaurantes"))
        filas_dos_rubros.append(fila(d, 40000, "Ocio", "Salidas"))

    res_dos = elegir_rubro_sin_presupuesto(filas_dos_rubros, cerrados3, set(), set(), "ARS")
    assert res_dos is not None
    assert res_dos.categoria == "Gastronomía"
    assert res_dos.monto == Decimal("60000")

    res_cub = elegir_rubro_sin_presupuesto(filas_dos_rubros, cerrados3, {"Gastronomía"}, set(), "ARS")
    assert res_cub is not None
    assert res_cub.categoria == "Ocio"
    assert res_cub.monto == Decimal("40000")

    res_nom = elegir_rubro_sin_presupuesto(filas_dos_rubros, cerrados3, set(), {"gastronomía"}, "ARS")
    assert res_nom is not None
    assert res_nom.categoria == "Ocio"
    assert res_nom.monto == Decimal("40000")

    # Supermercado 500.000 + Indumentaria > Ropa 100.000 con es_cuota por ciclo -> None
    filas_cuota = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        filas_cuota.append(fila(d, 500000, "Alimentación", "Supermercado"))
        filas_cuota.append(fila(d, 100000, "Indumentaria", "Ropa", es_cuota=True))
    assert elegir_rubro_sin_presupuesto(filas_cuota, cerrados3, set(), set(), "ARS") is None

    # Supermercado 500.000 + Tecnología > Equipos 10.000 común + Tecnología > Equipos 90.000 con es_cuota por ciclo -> None
    filas_tecno = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        filas_tecno.append(fila(d, 500000, "Alimentación", "Supermercado"))
        filas_tecno.append(fila(d, 10000, "Tecnología", "Equipos", es_cuota=False))
        filas_tecno.append(fila(d, 90000, "Tecnología", "Equipos", es_cuota=True))
    assert elegir_rubro_sin_presupuesto(filas_tecno, cerrados3, set(), set(), "ARS") is None

    # (a) Supermercado 500.000 + Tecnología > Equipos 60.000 común + Tecnología > Equipos 40.000 con es_cuota por ciclo
    # -> RubroSinPresupuesto("Tecnología", "ARS", 100.000, 100.000)
    filas_tecno_a = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        filas_tecno_a.append(fila(d, 500000, "Alimentación", "Supermercado"))
        filas_tecno_a.append(fila(d, 60000, "Tecnología", "Equipos", es_cuota=False))
        filas_tecno_a.append(fila(d, 40000, "Tecnología", "Equipos", es_cuota=True))
    res_tecno_a = elegir_rubro_sin_presupuesto(filas_tecno_a, cerrados3, set(), set(), "ARS")
    assert res_tecno_a == RubroSinPresupuesto("Tecnología", "ARS", Decimal("100000"), Decimal("100000"))

    # (b) Supermercado 500.000 + Hogar > Limpieza 40.000 + Hogar > Servicio doméstico 100.000 por ciclo -> None
    filas_hogar_b = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        filas_hogar_b.append(fila(d, 500000, "Alimentación", "Supermercado"))
        filas_hogar_b.append(fila(d, 40000, "Hogar", "Limpieza", es_cuota=False))
        filas_hogar_b.append(fila(d, 100000, "Hogar", "Servicio doméstico", es_cuota=False))
    assert elegir_rubro_sin_presupuesto(filas_hogar_b, cerrados3, set(), set(), "ARS") is None

    # (c) Supermercado 500.000 + Gastronomía > Restaurantes 60.000 en octubre y septiembre, y en agosto solo Gastronomía > Restaurantes 60.000 con es_cuota -> None
    filas_gastro_c = [
        fila(date(2026, 10, 10), 500000, "Alimentación", "Supermercado"),
        fila(date(2026, 10, 10), 60000, "Gastronomía", "Restaurantes", es_cuota=False),
        fila(date(2026, 9, 10), 500000, "Alimentación", "Supermercado"),
        fila(date(2026, 9, 10), 60000, "Gastronomía", "Restaurantes", es_cuota=False),
        fila(date(2026, 8, 10), 500000, "Alimentación", "Supermercado"),
        fila(date(2026, 8, 10), 60000, "Gastronomía", "Restaurantes", es_cuota=True),
    ]
    assert elegir_rubro_sin_presupuesto(filas_gastro_c, cerrados3, set(), set(), "ARS") is None

    # base sin Gastronomía en agosto -> None
    filas_sin_agosto = [
        f for f in base_filas
        if not (f.fecha == date(2026, 8, 10) and f.categoria == "Gastronomía")
    ]
    assert elegir_rubro_sin_presupuesto(filas_sin_agosto, cerrados3, set(), set(), "ARS") is None

    # base con cerrados3 = solo los dos primeros ciclos (y sus filas) -> None
    assert elegir_rubro_sin_presupuesto(base_filas, cerrados3[:2], set(), set(), "ARS") is None

    # Supermercado 500.000 + Hogar > Servicio doméstico 100.000 por ciclo -> None (excluida)
    filas_domestico = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        filas_domestico.append(fila(d, 500000, "Alimentación", "Supermercado"))
        filas_domestico.append(fila(d, 100000, "Hogar", "Servicio doméstico"))
    assert elegir_rubro_sin_presupuesto(filas_domestico, cerrados3, set(), set(), "ARS") is None

    # USD: base en dólares (Supermercado 950, Restaurantes 2 x 25) más base en pesos
    # -> con "USD" RubroSinPresupuesto("Gastronomía", "USD", 50, 50) y con "ARS" el de pesos
    usd_filas = []
    for d in [date(2026, 10, 10), date(2026, 9, 10), date(2026, 8, 10)]:
        usd_filas.append(fila(d, 950, "Alimentación", "Supermercado", moneda="USD"))
        usd_filas.append(fila(d, 25, "Gastronomía", "Restaurantes", moneda="USD"))
        usd_filas.append(fila(d, 25, "Gastronomía", "Restaurantes", moneda="USD"))
    mixto_filas = base_filas + usd_filas

    res_usd = elegir_rubro_sin_presupuesto(mixto_filas, cerrados3, set(), set(), "USD")
    assert res_usd == RubroSinPresupuesto("Gastronomía", "USD", Decimal("50"), Decimal("50"))

    res_ars = elegir_rubro_sin_presupuesto(mixto_filas, cerrados3, set(), set(), "ARS")
    assert res_ars == RubroSinPresupuesto("Gastronomía", "ARS", Decimal("50000"), Decimal("50000"))
