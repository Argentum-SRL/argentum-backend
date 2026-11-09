# -*- coding: utf-8 -*-
"""
tests/test_consejos_reglas.py
Tests unitarios de funciones puras para las reglas de consejos (fase_i1_consejos).
Sin base de datos, sin red, con valores exactos de la especificación.
"""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

from app.services.consejos_reglas import (
    FilaGasto,
    Consejo,
    FilaHistorial,
    DatosConsejos,
    evaluar_hormiga,
    evaluar_plata_quieta,
    evaluar_colchon,
    evaluar_comisiones,
    evaluar_pico,
    evaluar_sin_rubro,
    evaluar_sin_ingresos,
    evaluar_todos,
    a_precios_de_hoy,
    ordenar,
    elegir,
    comparar_medicion,
)


def _crear_base(k: Decimal = Decimal("1"), moneda: str = "ARS", fecha: date = date(2026, 10, 10)) -> list[FilaGasto]:
    """
    42 filas de egreso confirmadas del 10/10/2026 con montos × k:
    Transporte > Taxi y apps 9 × 2.500;
    Alimentación > Kiosco 3 × 1.500;
    Gastronomía > Cafetería 5 × 3.000;
    Alimentación > Supermercado 10 × 20.000;
    Alimentación > Verdulería 4 × 8.000;
    Alimentación > Carnicería 4 × 15.000;
    Gastronomía > Restaurantes 2 × 20.000;
    Gastronomía > Delivery 3 × 12.000;
    Vivienda > Luz 1 × 25.000;
    Vivienda > Alquiler 1 × 300.000
    Total = 735.000 * k; mediana = 10.000 * k
    """
    filas: list[FilaGasto] = []

    def add(cat: str, sub: str, cant: int, monto_unit: int):
        for _ in range(cant):
            filas.append(
                FilaGasto(
                    fecha=fecha,
                    monto=Decimal(str(monto_unit)) * k,
                    moneda=moneda,
                    categoria=cat,
                    subcategoria=sub,
                    es_espejo=False,
                    es_cuota=False,
                )
            )

    add("Transporte", "Taxi y apps", 9, 2500)
    add("Alimentación", "Kiosco", 3, 1500)
    add("Gastronomía", "Cafetería", 5, 3000)
    add("Alimentación", "Supermercado", 10, 20000)
    add("Alimentación", "Verdulería", 4, 8000)
    add("Alimentación", "Carnicería", 4, 15000)
    add("Gastronomía", "Restaurantes", 2, 20000)
    add("Gastronomía", "Delivery", 3, 12000)
    add("Vivienda", "Luz", 1, 25000)
    add("Vivienda", "Alquiler", 1, 300000)
    return filas


# ==============================================================================
# C1: GASTOS HORMIGA
# ==============================================================================

def test_c1_hormiga_base1():
    filas = _crear_base(Decimal("1"), "ARS")
    t90 = [f.monto for f in filas]
    c = evaluar_hormiga(filas, t90, "ARS", date(2026, 10, 1))
    assert c is not None
    assert c.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $38.000 por mes: $456.000 al año."
    assert c.clave == "hormiga/ARS/2026-10-01"
    assert c.ahorro == Decimal("91200.00")
    assert c.valores["total"] == "37500.00"


def test_c1_hormiga_limite_5_porciento():
    # (b) + Supermercado 15.000 (gasto 750.000): el mismo texto
    filas = _crear_base(Decimal("1"), "ARS")
    filas.append(
        FilaGasto(
            fecha=date(2026, 10, 10),
            monto=Decimal("15000"),
            moneda="ARS",
            categoria="Alimentación",
            subcategoria="Supermercado",
        )
    )
    t90 = [f.monto for f in filas]
    c = evaluar_hormiga(filas, t90, "ARS", date(2026, 10, 1))
    assert c is not None
    assert c.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $38.000 por mes: $456.000 al año."

    # + Supermercado 15.001: None
    filas[-1] = FilaGasto(
        fecha=date(2026, 10, 10),
        monto=Decimal("15001"),
        moneda="ARS",
        categoria="Alimentación",
        subcategoria="Supermercado",
    )
    c = evaluar_hormiga(filas, t90, "ARS", date(2026, 10, 1))
    assert c is None


def test_c1_hormiga_tickets90():
    filas = _crear_base(Decimal("1"), "ARS")
    # con tickets90 = [6.000]: mismo texto
    c1 = evaluar_hormiga(filas, [Decimal("6000")], "ARS", date(2026, 10, 1))
    assert c1 is not None
    assert c1.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $38.000 por mes: $456.000 al año."

    # con [5.998]: None
    c2 = evaluar_hormiga(filas, [Decimal("5998")], "ARS", date(2026, 10, 1))
    assert c2 is None


def test_c1_hormiga_minimo_20_compras():
    # (c) con tickets90 = [10.000]: Taxi 9 × 2.500, Cafetería 5 × 3.000, Supermercado 3 × 20.000, Luz 25.000 y Alquiler 300.000 (19 compras) → None; + Supermercado 20.000 (20 compras) → el texto de (a)
    filas: list[FilaGasto] = []
    for _ in range(9):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("2500"), "ARS", "Transporte", "Taxi y apps"))
    for _ in range(5):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("3000"), "ARS", "Gastronomía", "Cafetería"))
    for _ in range(3):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("20000"), "ARS", "Alimentación", "Supermercado"))
    filas.append(FilaGasto(date(2026, 10, 10), Decimal("25000"), "ARS", "Vivienda", "Luz"))
    filas.append(FilaGasto(date(2026, 10, 10), Decimal("300000"), "ARS", "Vivienda", "Alquiler"))
    assert len(filas) == 19

    c = evaluar_hormiga(filas, [Decimal("10000")], "ARS", date(2026, 10, 1))
    assert c is None

    # + Supermercado 20.000 (20 compras)
    filas.append(FilaGasto(date(2026, 10, 10), Decimal("20000"), "ARS", "Alimentación", "Supermercado"))
    assert len(filas) == 20
    c = evaluar_hormiga(filas, [Decimal("10000")], "ARS", date(2026, 10, 1))
    assert c is not None
    assert c.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $38.000 por mes: $456.000 al año."


def test_c1_hormiga_exclusiones():
    # (f) con tickets90 = [10.000]: base(1) sin el Alquiler + Verdulería 8 × 1.000 + Otros > Pagos a personas 5 × 1.000 + Indumentaria > Ropa 6 × 2.000 con es_cuota + Alimentación sin subcategoría 4 × 1.000 + un espejo Taxi de −2.500
    filas = [f for f in _crear_base(Decimal("1"), "ARS") if f.subcategoria != "Alquiler"]
    for _ in range(8):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("1000"), "ARS", "Alimentación", "Verdulería"))
    for _ in range(5):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("1000"), "ARS", "Otros", "Pagos a personas"))
    for _ in range(6):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("2000"), "ARS", "Indumentaria", "Ropa", es_cuota=True))
    for _ in range(4):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("1000"), "ARS", "Alimentación", None))
    filas.append(FilaGasto(date(2026, 10, 10), Decimal("-2500"), "ARS", "Transporte", "Taxi y apps", es_espejo=True))

    c = evaluar_hormiga(filas, [Decimal("10000")], "ARS", date(2026, 10, 1))
    assert c is not None
    assert c.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $35.000 por mes: $420.000 al año."


def test_c1_hormiga_variaciones_subcategorias():
    # Una sola: base(1) con Cafetería 3 × 3.000 y Taxi 15 × 2.500
    filas: list[FilaGasto] = []
    base_raw = _crear_base(Decimal("1"), "ARS")
    for f in base_raw:
        if f.subcategoria not in ("Cafetería", "Taxi y apps"):
            filas.append(f)
    for _ in range(3):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("3000"), "ARS", "Gastronomía", "Cafetería"))
    for _ in range(15):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("2500"), "ARS", "Transporte", "Taxi y apps"))

    t90 = [f.monto for f in filas]
    c1 = evaluar_hormiga(filas, t90, "ARS", date(2026, 10, 1))
    assert c1 is not None
    assert c1.texto == "Lo que gastás en Taxi y apps, de a poco, suma unos $38.000 por mes: $456.000 al año."

    # Tres: base(1) + Ocio > Hobbies y juegos 4 × 1.000
    filas3 = _crear_base(Decimal("1"), "ARS")
    for _ in range(4):
        filas3.append(FilaGasto(date(2026, 10, 10), Decimal("1000"), "ARS", "Ocio", "Hobbies y juegos"))
    t90_3 = [f.monto for f in filas3]
    c3 = evaluar_hormiga(filas3, t90_3, "ARS", date(2026, 10, 1))
    assert c3 is not None
    assert c3.texto == "Los gastos chicos y seguidos (Taxi y apps, Cafetería y otros) suman unos $42.000 por mes: $504.000 al año."

    # Dólares: base(0,001) en USD
    filas_usd = _crear_base(Decimal("0.001"), "USD")
    t90_usd = [f.monto for f in filas_usd]
    c_usd = evaluar_hormiga(filas_usd, t90_usd, "USD", date(2026, 10, 1))
    assert c_usd is not None
    assert c_usd.texto == "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos US$38 por mes: US$456 al año."


def test_c1_hormiga_ordenada_none():
    # Ordenada: Supermercado 5 × 40.000, Alquiler 300.000, Restaurantes 4 × 20.000, Indumentaria > Ropa 50.000, Vivienda > Luz 30.000, Taxi 3 × 2.500, Kiosco 3 × 1.500, Delivery 3 × 12.000, Ocio > Salidas 2 × 6.000 → None
    filas: list[FilaGasto] = []
    for _ in range(5):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("40000"), "ARS", "Alimentación", "Supermercado"))
    filas.append(FilaGasto(date(2026, 10, 10), Decimal("300000"), "ARS", "Vivienda", "Alquiler"))
    for _ in range(4):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("20000"), "ARS", "Gastronomía", "Restaurantes"))
    filas.append(FilaGasto(date(2026, 10, 10), Decimal("50000"), "ARS", "Indumentaria", "Ropa"))
    filas.append(FilaGasto(date(2026, 10, 10), Decimal("30000"), "ARS", "Vivienda", "Luz"))
    for _ in range(3):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("2500"), "ARS", "Transporte", "Taxi y apps"))
    for _ in range(3):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("1500"), "ARS", "Alimentación", "Kiosco"))
    for _ in range(3):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("12000"), "ARS", "Gastronomía", "Delivery"))
    for _ in range(2):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("6000"), "ARS", "Ocio", "Salidas"))

    t90 = [f.monto for f in filas]
    c = evaluar_hormiga(filas, t90, "ARS", date(2026, 10, 1))
    assert c is None


# ==============================================================================
# C2: PLATA QUIETA
# ==============================================================================

def test_c2_plata_quieta():
    c = evaluar_plata_quieta(
        libre_ars=Decimal("1000000"),
        gasto_tipico_ars=Decimal("500000"),
        tasa=Decimal("2.1"),
        mes="diciembre",
        fuente="REM de octubre",
        billetera_inversion=None,
        hoy=date(2026, 11, 2),
        inversiones_ars=Decimal("0"),
    )
    assert c is not None
    assert c.texto == "Tenés unos $500.000 que este mes no vas a necesitar. Con la inflación esperada (2,1% en diciembre, REM de octubre), pierden unos $11.000 por mes si quedan quietos. Fijate si tu billetera virtual tiene la opción de rendimiento o intereses."
    assert c.ahorro == Decimal("126000.00")

    # con billetera "Ahorro"
    c_ahorro = evaluar_plata_quieta(
        libre_ars=Decimal("1000000"),
        gasto_tipico_ars=Decimal("500000"),
        tasa=Decimal("2.1"),
        mes="diciembre",
        fuente="REM de octubre",
        billetera_inversion="Ahorro",
        hoy=date(2026, 11, 2),
        inversiones_ars=Decimal("0"),
    )
    assert c_ahorro is not None
    assert c_ahorro.texto.endswith(" Podés pasar una parte a Ahorro.")

    # Borde: libre 750.000 -> aparece
    c_borde = evaluar_plata_quieta(
        libre_ars=Decimal("750000"),
        gasto_tipico_ars=Decimal("500000"),
        tasa=Decimal("2.1"),
        mes="diciembre",
        fuente="REM de octubre",
        billetera_inversion=None,
        hoy=date(2026, 11, 2),
        inversiones_ars=Decimal("0"),
    )
    assert c_borde is not None

    # 749.999 -> None
    c_borde_fail = evaluar_plata_quieta(
        libre_ars=Decimal("749999"),
        gasto_tipico_ars=Decimal("500000"),
        tasa=Decimal("2.1"),
        mes="diciembre",
        fuente="REM de octubre",
        billetera_inversion=None,
        hoy=date(2026, 11, 2),
        inversiones_ars=Decimal("0"),
    )
    assert c_borde_fail is None

    # Tasa None -> None
    assert evaluar_plata_quieta(
        libre_ars=Decimal("1000000"),
        gasto_tipico_ars=Decimal("500000"),
        tasa=None,
        mes="diciembre",
        fuente="REM de octubre",
        billetera_inversion=None,
        hoy=date(2026, 11, 2),
        inversiones_ars=Decimal("0"),
    ) is None

    # Gasto típico 0 -> None
    assert evaluar_plata_quieta(
        libre_ars=Decimal("1000000"),
        gasto_tipico_ars=Decimal("0"),
        tasa=Decimal("2.1"),
        mes="diciembre",
        fuente="REM de octubre",
        billetera_inversion=None,
        hoy=date(2026, 11, 2),
        inversiones_ars=Decimal("0"),
    ) is None


# ==============================================================================
# C3: COLCHON
# ==============================================================================

def test_c3_colchon():
    c = evaluar_colchon(
        disponible=Decimal("300000"),
        gasto_tipico=Decimal("500000"),
        moneda="ARS",
        tiene_meta_colchon=False,
        plata_quieta_elegible=False,
        hoy=date(2026, 11, 2),
        convertido=False,
    )
    assert c is not None
    assert c.texto == "Si un mes no entrara plata, lo que tenés alcanza para unos 18 días de tus gastos. Un primer objetivo es medio mes: $250.000."
    assert c.accion_datos == {"nombre": "Imprevistos", "monto": "250000", "moneda": "ARS"}

    # 499.999 -> "unos 29 días"
    c_29 = evaluar_colchon(
        disponible=Decimal("499999"),
        gasto_tipico=Decimal("500000"),
        moneda="ARS",
        tiene_meta_colchon=False,
        plata_quieta_elegible=False,
        hoy=date(2026, 11, 2),
        convertido=False,
    )
    assert c_29 is not None
    assert "alcanza para unos 29 días de tus gastos" in c_29.texto

    # 500.000 -> None
    c_500 = evaluar_colchon(
        disponible=Decimal("500000"),
        gasto_tipico=Decimal("500000"),
        moneda="ARS",
        tiene_meta_colchon=False,
        plata_quieta_elegible=False,
        hoy=date(2026, 11, 2),
        convertido=False,
    )
    assert c_500 is None

    # 10.000 y -5.000 -> "hoy no tendrías con qué cubrir tus gastos"
    c_10 = evaluar_colchon(
        disponible=Decimal("10000"),
        gasto_tipico=Decimal("500000"),
        moneda="ARS",
        tiene_meta_colchon=False,
        plata_quieta_elegible=False,
        hoy=date(2026, 11, 2),
        convertido=False,
    )
    assert c_10 is not None
    assert c_10.texto == "Si un mes no entrara plata, hoy no tendrías con qué cubrir tus gastos. Un primer objetivo es medio mes: $250.000."

    c_neg = evaluar_colchon(
        disponible=Decimal("-5000"),
        gasto_tipico=Decimal("500000"),
        moneda="ARS",
        tiene_meta_colchon=False,
        plata_quieta_elegible=False,
        hoy=date(2026, 11, 2),
        convertido=False,
    )
    assert c_neg is not None
    assert c_neg.texto == "Si un mes no entrara plata, hoy no tendrías con qué cubrir tus gastos. Un primer objetivo es medio mes: $250.000."

    # Gasto típico 333.333 con 100.000 -> "... unos 9 días ... medio mes: $167.000."
    c_333 = evaluar_colchon(
        disponible=Decimal("100000"),
        gasto_tipico=Decimal("333333"),
        moneda="ARS",
        tiene_meta_colchon=False,
        plata_quieta_elegible=False,
        hoy=date(2026, 11, 2),
        convertido=False,
    )
    assert c_333 is not None
    assert "unos 9 días" in c_333.texto
    assert "medio mes: $167.000." in c_333.texto

    # Dólares convertido: 250 y 500 en USD -> "Si un mes no entrara plata, lo que tenés alcanza para unos 15 días de tus gastos (con tus pesos a dólar oficial). Un primer objetivo es medio mes: US$250."
    c_usd = evaluar_colchon(
        disponible=Decimal("250"),
        gasto_tipico=Decimal("500"),
        moneda="USD",
        tiene_meta_colchon=False,
        plata_quieta_elegible=False,
        hoy=date(2026, 11, 2),
        convertido=True,
    )
    assert c_usd is not None
    assert c_usd.texto == "Si un mes no entrara plata, lo que tenés alcanza para unos 15 días de tus gastos (con tus pesos a dólar oficial). Un primer objetivo es medio mes: US$250."

    # Con meta de colchón o con plata quieta elegible -> None
    assert evaluar_colchon(Decimal("300000"), Decimal("500000"), "ARS", tiene_meta_colchon=True, plata_quieta_elegible=False, hoy=date(2026, 11, 2), convertido=False) is None
    assert evaluar_colchon(Decimal("300000"), Decimal("500000"), "ARS", tiene_meta_colchon=False, plata_quieta_elegible=True, hoy=date(2026, 11, 2), convertido=False) is None


# ==============================================================================
# C4: COMISIONES
# ==============================================================================

def test_c4_comisiones():
    # 3 ciclos de Supermercado 985.000 + Comisiones 15.000
    ciclo = [
        FilaGasto(date(2026, 10, 10), Decimal("985000"), "ARS", "Alimentación", "Supermercado"),
        FilaGasto(date(2026, 10, 10), Decimal("15000"), "ARS", "Banco", "Comisiones"),
    ]
    c3 = evaluar_comisiones([ciclo, ciclo, ciclo], date(2026, 11, 2))
    assert c3 is not None
    assert c3.texto == "En los últimos 3 meses pagaste $45.000 en comisiones del banco: $180.000 al año. Una caja de ahorro en pesos básica no tiene costo de mantenimiento: preguntá en tu banco si te conviene pasarte."
    assert c3.ahorro == Decimal("180000.00")

    # con 1 ciclo
    c1 = evaluar_comisiones([ciclo], date(2026, 11, 2))
    assert c1 is not None
    assert c1.texto == "En el último mes pagaste $15.000 en comisiones del banco: $180.000 al año. Una caja de ahorro en pesos básica no tiene costo de mantenimiento: preguntá en tu banco si te conviene pasarte."

    # Borde: 3 ciclos de 990.000 + 10.000 -> aparece
    ciclo_borde = [
        FilaGasto(date(2026, 10, 10), Decimal("990000"), "ARS", "Alimentación", "Supermercado"),
        FilaGasto(date(2026, 10, 10), Decimal("10000"), "ARS", "Banco", "Comisiones"),
    ]
    assert evaluar_comisiones([ciclo_borde, ciclo_borde, ciclo_borde], date(2026, 11, 2)) is not None

    # si el tercero tiene 9.999 -> None
    ciclo_borde_menor = [
        FilaGasto(date(2026, 10, 10), Decimal("990000"), "ARS", "Alimentación", "Supermercado"),
        FilaGasto(date(2026, 10, 10), Decimal("9999"), "ARS", "Banco", "Comisiones"),
    ]
    assert evaluar_comisiones([ciclo_borde, ciclo_borde, ciclo_borde_menor], date(2026, 11, 2)) is None

    # Comisiones en USD no cuentan
    ciclo_usd = [
        FilaGasto(date(2026, 10, 10), Decimal("100"), "USD", "Banco", "Comisiones"),
    ]
    assert evaluar_comisiones([ciclo_usd, ciclo_usd, ciclo_usd], date(2026, 11, 2)) is None


# ==============================================================================
# C5: PICO
# ==============================================================================

def test_c5_pico():
    # cerrados de Ocio > Salidas [40.000, 42.000, 38.000, 41.000, 39.000, 40.000], gasto típico 1.000.000
    montos = [40000, 42000, 38000, 41000, 39000, 40000]
    cerrados = [
        [FilaGasto(date(2026, 5 + i, 10), Decimal(str(m)), "ARS", "Ocio", "Salidas")]
        for i, m in enumerate(montos)
    ]
    # en curso 80.000 -> "Este mes llevás $80.000 en Ocio; lo normal tuyo es $40.000.", ahorro 40.000
    actual_80 = [FilaGasto(date(2026, 11, 5), Decimal("80000"), "ARS", "Ocio", "Salidas")]
    c = evaluar_pico(actual_80, cerrados, Decimal("1000000"), "ARS", date(2026, 11, 1))
    assert c is not None
    assert c.texto == "Este mes llevás $80.000 en Ocio; lo normal tuyo es $40.000."
    assert c.ahorro == Decimal("40000.00")

    # 50.000 -> aparece
    actual_50 = [FilaGasto(date(2026, 11, 5), Decimal("50000"), "ARS", "Ocio", "Salidas")]
    assert evaluar_pico(actual_50, cerrados, Decimal("1000000"), "ARS", date(2026, 11, 1)) is not None

    # 49.999 -> None
    actual_49 = [FilaGasto(date(2026, 11, 5), Decimal("49999"), "ARS", "Ocio", "Salidas")]
    assert evaluar_pico(actual_49, cerrados, Decimal("1000000"), "ARS", date(2026, 11, 1)) is None

    # Gasto típico 100.000: 43.000 -> None; 43.001 -> "Este mes llevás $43.000 en Ocio; lo normal tuyo es $40.000."
    actual_43 = [FilaGasto(date(2026, 11, 5), Decimal("43000"), "ARS", "Ocio", "Salidas")]
    assert evaluar_pico(actual_43, cerrados, Decimal("100000"), "ARS", date(2026, 11, 1)) is None

    actual_43001 = [FilaGasto(date(2026, 11, 5), Decimal("43001"), "ARS", "Ocio", "Salidas")]
    c_43001 = evaluar_pico(actual_43001, cerrados, Decimal("100000"), "ARS", date(2026, 11, 1))
    assert c_43001 is not None
    assert c_43001.texto == "Este mes llevás $43.000 en Ocio; lo normal tuyo es $40.000."

    # Solo 3 cerrados -> None
    assert evaluar_pico(actual_80, cerrados[:3], Decimal("1000000"), "ARS", date(2026, 11, 1)) is None

    # cerrados [40.000, 42.000, 38.000, 0, 0, 0] -> None
    cerrados_con_ceros = cerrados[:3] + [[], [], []]
    assert evaluar_pico(actual_80, cerrados_con_ceros, Decimal("1000000"), "ARS", date(2026, 11, 1)) is None

    # Dólares (todo ÷ 100, gasto típico 10.000) con 800 -> "Este mes llevás US$800 en Ocio; lo normal tuyo es US$400."
    cerrados_usd = [
        [FilaGasto(date(2026, 5 + i, 10), Decimal(str(m)) / Decimal("100"), "USD", "Ocio", "Salidas")]
        for i, m in enumerate(montos)
    ]
    actual_usd = [FilaGasto(date(2026, 11, 5), Decimal("800"), "USD", "Ocio", "Salidas")]
    c_usd = evaluar_pico(actual_usd, cerrados_usd, Decimal("10000"), "USD", date(2026, 11, 1))
    assert c_usd is not None
    assert c_usd.texto == "Este mes llevás US$800 en Ocio; lo normal tuyo es US$400."

    # Supermercado con el mismo salto -> None
    actual_super = [FilaGasto(date(2026, 11, 5), Decimal("80000"), "ARS", "Alimentación", "Supermercado")]
    cerrados_super = [
        [FilaGasto(date(2026, 5 + i, 10), Decimal(str(m)), "ARS", "Alimentación", "Supermercado")]
        for i, m in enumerate(montos)
    ]
    assert evaluar_pico(actual_super, cerrados_super, Decimal("1000000"), "ARS", date(2026, 11, 1)) is None


# ==============================================================================
# C14: SIN RUBRO
# ==============================================================================

def test_c14_sin_rubro():
    # Supermercado 85.000 + 5 × 3.000 en Otros sin subcategoría
    filas = [FilaGasto(date(2026, 10, 10), Decimal("85000"), "ARS", "Alimentación", "Supermercado")]
    for _ in range(5):
        filas.append(FilaGasto(date(2026, 10, 10), Decimal("3000"), "ARS", "Otros", None))

    c = evaluar_sin_rubro(filas, "ARS", date(2026, 10, 1))
    assert c is not None
    assert c.texto == "El 15% de lo que gastaste el mes pasado quedó sin rubro. Si lo clasificás, tus números y estos consejos te van a servir más."

    # Supermercado 88.000 + 4 × 3.000 -> None (solo 4 compras sin rubro)
    filas_4 = [FilaGasto(date(2026, 10, 10), Decimal("88000"), "ARS", "Alimentación", "Supermercado")]
    for _ in range(4):
        filas_4.append(FilaGasto(date(2026, 10, 10), Decimal("3000"), "ARS", "Otros", None))
    assert evaluar_sin_rubro(filas_4, "ARS", date(2026, 10, 1)) is None

    # con 85.001 -> None (< 15%)
    filas_85001 = [FilaGasto(date(2026, 10, 10), Decimal("85001"), "ARS", "Alimentación", "Supermercado")]
    for _ in range(5):
        filas_85001.append(FilaGasto(date(2026, 10, 10), Decimal("3000"), "ARS", "Otros", None))
    assert evaluar_sin_rubro(filas_85001, "ARS", date(2026, 10, 1)) is None

    # 5 × 3.000 en Alimentación sin subcategoría -> None
    filas_alim = [FilaGasto(date(2026, 10, 10), Decimal("85000"), "ARS", "Alimentación", "Supermercado")]
    for _ in range(5):
        filas_alim.append(FilaGasto(date(2026, 10, 10), Decimal("3000"), "ARS", "Alimentación", None))
    assert evaluar_sin_rubro(filas_alim, "ARS", date(2026, 10, 1)) is None

    # Dólares (÷ 100) -> el mismo texto
    filas_usd = [FilaGasto(date(2026, 10, 10), Decimal("850"), "USD", "Alimentación", "Supermercado")]
    for _ in range(5):
        filas_usd.append(FilaGasto(date(2026, 10, 10), Decimal("30"), "USD", "Otros", None))
    c_usd = evaluar_sin_rubro(filas_usd, "USD", date(2026, 10, 1))
    assert c_usd is not None
    assert c_usd.texto == "El 15% de lo que gastaste el mes pasado quedó sin rubro. Si lo clasificás, tus números y estos consejos te van a servir más."


# ==============================================================================
# C15: SIN INGRESOS
# ==============================================================================

def test_c15_sin_ingresos():
    hoy = date(2026, 11, 2)
    c = evaluar_sin_ingresos(10, 0, hoy)
    assert c is not None
    assert c.texto == "Cargaste tus gastos pero ningún ingreso. Cuando cobres, registralo (también por WhatsApp): sin eso no puedo decirte cuánto te queda libre."

    assert evaluar_sin_ingresos(9, 0, hoy) is None
    assert evaluar_sin_ingresos(10, 1, hoy) is None


# ==============================================================================
# EVALUAR TODOS
# ==============================================================================

def test_evaluar_todos():
    filas_base = _crear_base(Decimal("1"), "ARS")
    datos = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=None,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[(date(2026, 10, 1), date(2026, 10, 31))],
        filas=filas_base,
        ingresos_45=1,
        libre_ars=Decimal("300000"),
        saldo_hoy_ars=Decimal("900000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1000"),
        tasa_inflacion=Decimal("2.1"),
        mes_inflacion="diciembre",
        fuente_inflacion="REM de octubre",
        billetera_inversion=None,
        tiene_meta_colchon=True,  # Para que no emita colchon
    )
    # con cobertura None o 100 -> exactamente [hormiga]
    res_none = evaluar_todos(datos)
    assert len(res_none) == 1
    assert res_none[0].tipo == "hormiga"

    from dataclasses import replace
    res_100 = evaluar_todos(replace(datos, cobertura=100))
    assert len(res_100) == 1
    assert res_100[0].tipo == "hormiga"

    # con datos_viejos True -> []
    assert evaluar_todos(replace(datos, datos_viejos=True)) == []

    # T-1: Cobertura y factores en evaluar_todos
    datos_t1 = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=None,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[(date(2026, 10, 1), date(2026, 10, 31))],
        filas=filas_base,
        ingresos_45=1,
        libre_ars=Decimal("1200000"),
        saldo_hoy_ars=Decimal("1300000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1000"),
        tasa_inflacion=Decimal("2.1"),
        mes_inflacion="diciembre",
        fuente_inflacion="REM de octubre",
        billetera_inversion=None,
        tiene_meta_colchon=False,
        factor_cobertura=Decimal("1"),
        ipc={},
    )
    texto_hormiga = "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $38.000 por mes: $456.000 al año."

    # (a) libre 1.200.000, saldo 1.300.000
    # cobertura None (factor 1) -> exactamente [hormiga, plata_quieta]
    res_a_none = evaluar_todos(datos_t1)
    assert [c.tipo for c in res_a_none] == ["hormiga", "plata_quieta"]
    assert res_a_none[0].texto == texto_hormiga
    assert res_a_none[1].texto == (
        "Tenés unos $465.000 que este mes no vas a necesitar. Con la inflación esperada "
        "(2,1% en diciembre, REM de octubre), pierden unos $10.000 por mes si quedan quietos. "
        "Fijate si tu billetera virtual tiene la opción de rendimiento o intereses."
    )

    # cobertura 80 (factor 1.25) -> exactamente [hormiga]
    res_a_80 = evaluar_todos(replace(datos_t1, cobertura=80, factor_cobertura=Decimal("1.25")))
    assert [c.tipo for c in res_a_80] == ["hormiga"]
    assert res_a_80[0].texto == texto_hormiga

    # cobertura 50 (factor 2) -> exactamente [hormiga, colchon]
    res_a_50 = evaluar_todos(replace(datos_t1, cobertura=50, factor_cobertura=Decimal("2")))
    assert [c.tipo for c in res_a_50] == ["hormiga", "colchon"]
    assert res_a_50[0].texto == texto_hormiga
    assert res_a_50[1].texto == (
        "Si un mes no entrara plata, lo que tenés alcanza para unos 26 días de tus gastos. "
        "Un primer objetivo es medio mes: $735.000."
    )

    # cobertura 49 -> []
    assert evaluar_todos(replace(datos_t1, cobertura=49, factor_cobertura=Decimal("2"))) == []

    # (b) libre 300.000, saldo 600.000
    datos_b = replace(datos_t1, libre_ars=Decimal("300000"), saldo_hoy_ars=Decimal("600000"))

    # cobertura None -> [hormiga, colchon]
    res_b_none = evaluar_todos(datos_b)
    assert [c.tipo for c in res_b_none] == ["hormiga", "colchon"]
    assert res_b_none[0].texto == texto_hormiga
    assert "alcanza para unos 24 días de tus gastos. Un primer objetivo es medio mes: $368.000." in res_b_none[1].texto

    # cobertura 80 (factor 1.25) -> [hormiga, colchon]
    res_b_80 = evaluar_todos(replace(datos_b, cobertura=80, factor_cobertura=Decimal("1.25")))
    assert [c.tipo for c in res_b_80] == ["hormiga", "colchon"]
    assert res_b_80[0].texto == texto_hormiga
    assert "alcanza para unos 19 días de tus gastos. Un primer objetivo es medio mes: $460.000." in res_b_80[1].texto

    # La "ordenada" en un cerrado con ciclo actual, libre 300.000, etc. -> [] (sin falsos positivos)
    filas_ord: list[FilaGasto] = []
    for _ in range(5):
        filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("40000"), "ARS", "Alimentación", "Supermercado"))
    filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("300000"), "ARS", "Vivienda", "Alquiler"))
    for _ in range(4):
        filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("20000"), "ARS", "Gastronomía", "Restaurantes"))
    filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("50000"), "ARS", "Indumentaria", "Ropa"))
    filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("30000"), "ARS", "Vivienda", "Luz"))
    for _ in range(3):
        filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("2500"), "ARS", "Transporte", "Taxi y apps"))
    for _ in range(3):
        filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("1500"), "ARS", "Alimentación", "Kiosco"))
    for _ in range(3):
        filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("12000"), "ARS", "Gastronomía", "Delivery"))
    for _ in range(2):
        filas_ord.append(FilaGasto(date(2026, 10, 10), Decimal("6000"), "ARS", "Ocio", "Salidas"))

    datos_ord = replace(datos, filas=filas_ord)
    assert evaluar_todos(datos_ord) == []


def test_evaluar_todos_mixto():
    # Mixto: base(1) en pesos + base(0,001) en dólares -> un consejo por moneda con esos dos textos
    filas_ars = _crear_base(Decimal("1"), "ARS")
    filas_usd = _crear_base(Decimal("0.001"), "USD")
    filas_mixto = filas_ars + filas_usd

    datos_mixto = DatosConsejos(
        hoy=date(2026, 11, 2),
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[(date(2026, 10, 1), date(2026, 10, 31))],
        filas=filas_mixto,
        ingresos_45=1,
        libre_ars=Decimal("300000"),
        saldo_hoy_ars=Decimal("900000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1000"),
        tasa_inflacion=Decimal("2.1"),
        mes_inflacion="diciembre",
        fuente_inflacion="REM de octubre",
        billetera_inversion=None,
        tiene_meta_colchon=True,
    )
    candidatos = evaluar_todos(datos_mixto)
    hormigas = [c for c in candidatos if c.tipo == "hormiga"]
    assert len(hormigas) == 2
    textos = {h.texto for h in hormigas}
    assert "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos $38.000 por mes: $456.000 al año." in textos
    assert "Los gastos chicos y seguidos (Taxi y apps y Cafetería) suman unos US$38 por mes: US$456 al año." in textos


def test_t2_ajuste_inflacion_y_pico():
    ipc = {
        "2026-04": Decimal("100"),
        "2026-05": Decimal("102"),
        "2026-06": Decimal("104.04"),
        "2026-07": Decimal("106.1208"),
        "2026-08": Decimal("108.243216"),
        "2026-09": Decimal("110.40808032"),
    }
    hoy = date(2026, 10, 15)
    filas_seis = [
        FilaGasto(date(2026, m, 10), Decimal("40000"), "ARS", "Ocio", "Salidas")
        for m in (4, 5, 6, 7, 8, 9)
    ]

    # a_precios_de_hoy de esos seis en orden abril a septiembre
    ajustadas = a_precios_de_hoy(filas_seis, ipc, hoy)
    esperadas = [
        Decimal("44163.23"),
        Decimal("43297.29"),
        Decimal("42448.32"),
        Decimal("41616.00"),
        Decimal("40800.00"),
        Decimal("40000.00"),
    ]
    assert [f.monto for f in ajustadas] == esperadas

    # una fila del 10/10/2026 queda igual
    f_oct = FilaGasto(date(2026, 10, 10), Decimal("40000"), "ARS", "Ocio", "Salidas")
    assert a_precios_de_hoy([f_oct], ipc, hoy)[0].monto == Decimal("40000")

    # una fila USD queda igual
    f_usd = FilaGasto(date(2026, 4, 10), Decimal("40"), "USD", "Ocio", "Salidas")
    assert a_precios_de_hoy([f_usd], ipc, hoy)[0].monto == Decimal("40")

    # con ipc {} todo igual
    assert a_precios_de_hoy(filas_seis, {}, hoy) == filas_seis

    # evaluar_pico con los cerrados ajustados, gasto típico 100.000, ARS, inicio 01/10/2026:
    cerrados_ajustados = [[f] for f in ajustadas]
    gasto_tipico = Decimal("100000")
    inicio_oct = date(2026, 10, 1)

    # en curso 44.000 -> None
    filas_44 = [FilaGasto(date(2026, 10, 10), Decimal("44000"), "ARS", "Ocio", "Salidas")]
    assert evaluar_pico(filas_44, cerrados_ajustados, gasto_tipico, "ARS", inicio_oct) is None

    # en curso 48.000 -> "Este mes llevás $48.000 en Ocio; lo normal tuyo es $42.000.", valores mediana "42032.16"
    filas_48 = [FilaGasto(date(2026, 10, 10), Decimal("48000"), "ARS", "Ocio", "Salidas")]
    c_48 = evaluar_pico(filas_48, cerrados_ajustados, gasto_tipico, "ARS", inicio_oct)
    assert c_48 is not None
    assert c_48.texto == "Este mes llevás $48.000 en Ocio; lo normal tuyo es $42.000."
    assert c_48.valores["mediana"] == "42032.16"

    # Sin ajustar (ipc {}): en curso 44.000 -> "Este mes llevás $44.000 en Ocio; lo normal tuyo es $40.000."
    cerrados_sin_ajustar = [[f] for f in filas_seis]
    c_sin = evaluar_pico(filas_44, cerrados_sin_ajustar, gasto_tipico, "ARS", inicio_oct)
    assert c_sin is not None
    assert c_sin.texto == "Este mes llevás $44.000 en Ocio; lo normal tuyo es $40.000."

    # evaluar_todos con esos seis cerrados, ipc y fila de octubre de 44.000:
    # da sin pico; con mutación 25 aparece pico.
    # Cerrados ordenados más nuevo primero: sep, ago, jul, jun, may, abr
    cerrados_periodos = [
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 4, 1), date(2026, 4, 30)),
    ]
    datos_c5 = DatosConsejos(
        hoy=hoy,
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(date(2026, 10, 1), date(2026, 10, 31)),
        cerrados=cerrados_periodos,
        filas=filas_seis + filas_44,
        ingresos_45=1,
        libre_ars=Decimal("0"),
        saldo_hoy_ars=Decimal("0"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1000"),
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=True,
        factor_cobertura=Decimal("1"),
        ipc=ipc,
    )
    res_c5 = evaluar_todos(datos_c5)
    assert not any(c.tipo == "pico" for c in res_c5)


# ==============================================================================
# ORDENAR
# ==============================================================================

def test_ordenar():
    c_hormiga = Consejo(tipo="hormiga", clave="h", moneda="ARS", texto="t", ahorro=Decimal("91200.00"))
    c_comisiones = Consejo(tipo="comisiones", clave="c", moneda="ARS", texto="t", ahorro=Decimal("180000.00"))
    c_pico = Consejo(tipo="pico", clave="p", moneda="ARS", texto="t", ahorro=Decimal("40000.00"))
    c_sin_rubro = Consejo(tipo="sin_rubro", clave="sr", moneda="ARS", texto="t", ahorro=None)

    lista = [c_hormiga, c_comisiones, c_pico, c_sin_rubro]

    # default: [comisiones, hormiga, pico, sin_rubro]
    assert [c.tipo for c in ordenar(lista, [], Decimal("1000"))] == ["comisiones", "hormiga", "pico", "sin_rubro"]

    # con prioridades [7] -> [comisiones, sin_rubro, hormiga, pico]
    assert [c.tipo for c in ordenar(lista, [7], Decimal("1000"))] == ["comisiones", "sin_rubro", "hormiga", "pico"]

    # con [1] -> [sin_rubro, comisiones, hormiga, pico]
    assert [c.tipo for c in ordenar(lista, [1], Decimal("1000"))] == ["sin_rubro", "comisiones", "hormiga", "pico"]

    # sumando colchon -> primero colchon
    c_colchon = Consejo(tipo="colchon", clave="col", moneda="ARS", texto="t", ahorro=None)
    lista_con_colchon = lista + [c_colchon]
    assert [c.tipo for c in ordenar(lista_con_colchon, [], Decimal("1000"))][0] == "colchon"

    # sumando plata_quieta (126.000) con [3] -> [plata_quieta, colchon, comisiones, hormiga, pico, sin_rubro]
    c_plata_quieta = Consejo(tipo="plata_quieta", clave="pq", moneda="ARS", texto="t", ahorro=Decimal("126000.00"))
    lista_con_pq = lista_con_colchon + [c_plata_quieta]
    assert [c.tipo for c in ordenar(lista_con_pq, [3], Decimal("1000"))] == [
        "plata_quieta", "colchon", "comisiones", "hormiga", "pico", "sin_rubro"
    ]

    # Hormiga en USD con ahorro 91,2 y cotización 1.000 vale 91.200 al ordenar
    c_hormiga_usd = Consejo(tipo="hormiga", clave="h_usd", moneda="USD", texto="t", ahorro=Decimal("91.20"))
    lista_usd = [c_pico, c_hormiga_usd]  # pico 40.000 vs hormiga_usd 91.200
    assert [c.tipo for c in ordenar(lista_usd, [], Decimal("1000"))] == ["hormiga", "pico"]


# ==============================================================================
# ELEGIR
# ==============================================================================

def test_elegir():
    c_comisiones = Consejo(tipo="comisiones", clave="c", moneda="ARS", texto="t", ahorro=Decimal("180000.00"))
    c_hormiga = Consejo(tipo="hormiga", clave="h", moneda="ARS", texto="t", ahorro=Decimal("91200.00"))
    c_pico = Consejo(tipo="pico", clave="p", moneda="ARS", texto="t", ahorro=Decimal("40000.00"))
    c_sin_rubro = Consejo(tipo="sin_rubro", clave="sr", moneda="ARS", texto="t", ahorro=None)

    candidatos = [c_hormiga, c_comisiones, c_pico, c_sin_rubro]

    # elección con respuesta "sirvio" el mismo día de cada emisión:
    # 02/11 comisiones
    historial: list[FilaHistorial] = []
    el1 = elegir(candidatos, historial, set(), [], date(2026, 11, 2), Decimal("1000"))
    assert el1 is not None and el1.tipo == "comisiones"

    historial.append(FilaHistorial(tipo="comisiones", clave="c", fecha_emision=date(2026, 11, 2), respuesta="sirvio", fecha_vence=date(2026, 11, 9)))

    # 03/11 hormiga
    el2 = elegir(candidatos, historial, set(), [], date(2026, 11, 3), Decimal("1000"))
    assert el2 is not None and el2.tipo == "hormiga"

    historial.append(FilaHistorial(tipo="hormiga", clave="h", fecha_emision=date(2026, 11, 3), respuesta="sirvio", fecha_vence=date(2026, 11, 10)))

    # 04/11 y 05/11 None (tope semanal de 2 alcanzado)
    assert elegir(candidatos, historial, set(), [], date(2026, 11, 4), Decimal("1000")) is None
    assert elegir(candidatos, historial, set(), [], date(2026, 11, 5), Decimal("1000")) is None

    # 09/11 pico (la semana termina 7 días después de 02/11: el 02/11 ya no cae en > 09/11 - 7 días = 02/11)
    el3 = elegir(candidatos, historial, set(), [], date(2026, 11, 9), Decimal("1000"))
    assert el3 is not None and el3.tipo == "pico"

    # Hormiga emitida (otra clave) el 03/10: 01/11 None, 02/11 sí
    hist_h = [FilaHistorial(tipo="hormiga", clave="otra", fecha_emision=date(2026, 10, 3), respuesta="sirvio", fecha_vence=date(2026, 10, 10))]
    assert elegir([c_hormiga], hist_h, set(), [], date(2026, 11, 1), Decimal("1000")) is None
    assert elegir([c_hormiga], hist_h, set(), [], date(2026, 11, 2), Decimal("1000")) is not None

    # Sin rubro emitido el 03/09: 01/11 None, 02/11 sí
    hist_sr = [FilaHistorial(tipo="sin_rubro", clave="otra", fecha_emision=date(2026, 9, 3), respuesta="sirvio", fecha_vence=date(2026, 9, 10))]
    assert elegir([c_sin_rubro], hist_sr, set(), [], date(2026, 11, 1), Decimal("1000")) is None
    assert elegir([c_sin_rubro], hist_sr, set(), [], date(2026, 11, 2), Decimal("1000")) is not None

    # Con comisiones emitida y respondida el 02/11, otra elección el mismo 02/11 -> None
    hist_mismo_dia = [FilaHistorial(tipo="comisiones", clave="c", fecha_emision=date(2026, 11, 2), respuesta="sirvio", fecha_vence=date(2026, 11, 9))]
    assert elegir([c_hormiga], hist_mismo_dia, set(), [], date(2026, 11, 2), Decimal("1000")) is None

    # Uno activo emitido 01/11 (vence 08/11): 07/11 None; 08/11 sí
    hist_activo = [FilaHistorial(tipo="comisiones", clave="c", fecha_emision=date(2026, 11, 1), respuesta=None, fecha_vence=date(2026, 11, 8))]
    assert elegir([c_hormiga], hist_activo, set(), [], date(2026, 11, 7), Decimal("1000")) is None
    assert elegir([c_hormiga], hist_activo, set(), [], date(2026, 11, 8), Decimal("1000")) is not None

    # Tipo silenciado -> None
    assert elegir([c_hormiga], [], {"hormiga"}, [], date(2026, 11, 2), Decimal("1000")) is None

    # La misma (tipo, clave) emitida hace 90 días -> None
    hist_repetida = [FilaHistorial(tipo="hormiga", clave="h", fecha_emision=date(2026, 8, 1), respuesta="sirvio", fecha_vence=date(2026, 8, 8))]
    assert elegir([c_hormiga], hist_repetida, set(), [], date(2026, 11, 2), Decimal("1000")) is None


# ==============================================================================
# COMPARAR MEDICIÓN
# ==============================================================================

def test_comparar_medicion():
    # hormiga (37.500, 22.000) mejoro y (37.500, 37.500) igual
    assert comparar_medicion("hormiga", Decimal("37500"), Decimal("22000")) == "mejoro"
    assert comparar_medicion("hormiga", Decimal("37500"), Decimal("37500")) == "igual"

    # pico (40.000, 40.000) mejoro y (40.000, 40.001) igual
    assert comparar_medicion("pico", Decimal("40000"), Decimal("40000")) == "mejoro"
    assert comparar_medicion("pico", Decimal("40000"), Decimal("40001")) == "igual"

    # sin_rubro (15, 14) mejoro
    assert comparar_medicion("sin_rubro", Decimal("15"), Decimal("14")) == "mejoro"

    # comisiones (15.000, 15.000) igual
    assert comparar_medicion("comisiones", Decimal("15000"), Decimal("15000")) == "igual"

    # colchon (0, 1) mejoro y (0, 0) igual
    assert comparar_medicion("colchon", Decimal("0"), Decimal("1")) == "mejoro"
    assert comparar_medicion("colchon", Decimal("0"), Decimal("0")) == "igual"

    # plata_quieta (5, 5) igual
    assert comparar_medicion("plata_quieta", Decimal("5"), Decimal("5")) == "igual"


# ==============================================================================
# CHEQUEO ESTÁTICO DE AISLAMIENTO
# ==============================================================================

def test_aislamiento_estatico_whatsapp_y_notificaciones():
    """Ningún .py de app/routers/whatsapp*, app/services/whatsapp_service.py ni app/services/notificacion_*.py importa consejos_."""
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    archivos_a_revisar: list[str] = []

    routers_dir = os.path.join(base_dir, "routers")
    for f in os.listdir(routers_dir):
        if f.startswith("whatsapp") and f.endswith(".py"):
            archivos_a_revisar.append(os.path.join(routers_dir, f))

    services_dir = os.path.join(base_dir, "services")
    for f in os.listdir(services_dir):
        if (f == "whatsapp_service.py" or f.startswith("notificacion_")) and f.endswith(".py"):
            archivos_a_revisar.append(os.path.join(services_dir, f))

    assert len(archivos_a_revisar) > 0

    for path in archivos_a_revisar:
        with open(path, "r", encoding="utf-8") as archivo:
            contenido = archivo.read()
            assert "consejos_" not in contenido, f"Violación de aislamiento: {path} importa consejos_"
