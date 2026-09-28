"""
Tests unitarios del motor financiero: Cálculo de Ingreso Habitual e Ingreso Esperado.
Ubicación: tests/motor/test_ingreso_habitual.py

Cubre cada una de las reglas de decisión de la Fase 2b:
1. regular fijo
2. regular con aumentos (toma el último cobro)
3. intermitente (varios cobros por ciclo con total estable)
4. variable (promedio de los 3 totales más bajos)
5. variable con menos de 4 ciclos (toma el más bajo)
6. separación jubilación + bono (dos series habituales)
7. aguinaldo solo con historia en los últimos 12 meses
8. fecha de aguinaldo copiada del último del mismo semestre
9. sin datos por pocos ciclos (< 3 ciclos completos)
10. sin datos por irregular (< 80% de presencia)
11. ingreso esperado del ciclo cuando ya se cobró
12. ingreso esperado del ciclo cuando todavía no se cobró
13. USD separado de ARS
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion
from app.models.usuario import Moneda
from app.services.definiciones_service import ContextoDefiniciones
from app.services.ingreso_habitual_service import (
    calcular_ingreso_esperado_ciclo_en_memoria,
    calcular_ingreso_habitual_en_memoria,
)


class MockCategoria:
    def __init__(self, nombre: str):
        self.id = f"cat-{nombre.lower().replace(' ', '_')}"
        self.nombre = nombre


class MockSubcategoria:
    def __init__(self, nombre: str):
        self.id = f"sub-{nombre.lower().replace(' ', '_')}"
        self.nombre = nombre


class MockTransaccion:
    def __init__(
        self,
        fecha: date,
        monto: Decimal | float | int,
        moneda: str = "ARS",
        categoria: str = "Empleo",
        subcategoria: str = "Sueldo",
        tipo: str = "ingreso",
        descripcion: str = "",
    ):
        self.id = str(uuid4())
        self.fecha = fecha
        self.monto = Decimal(str(monto))
        self.moneda = Moneda.USD if moneda == "USD" else Moneda.ARS
        self.categoria = MockCategoria(categoria)
        self.subcategoria = MockSubcategoria(subcategoria)
        self.categoria_id = self.categoria.id
        self.subcategoria_id = self.subcategoria.id
        self.tipo = TipoTransaccion.INGRESO if tipo == "ingreso" else TipoTransaccion.EGRESO
        self.es_padre_cuotas = False
        self.movimiento_meta_id = None
        self.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
        self.descripcion = descripcion or f"{categoria} {subcategoria}"
        self.metodo_pago = None
        self.pago_resumen_vencimiento = None


def _generar_ciclos_mensuales(anio_inicio: int, mes_inicio: int, cantidad: int) -> list[tuple[date, date]]:
    """Genera lista de ciclos mensuales cerrados (desde día 1 a fin de mes)."""
    import calendar
    ciclos = []
    y, m = anio_inicio, mes_inicio
    for _ in range(cantidad):
        ultimo_dia = calendar.monthrange(y, m)[1]
        ciclos.append((date(y, m, 1), date(y, m, ultimo_dia)))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return ciclos


def _ctx(hoy: date) -> ContextoDefiniciones:
    return ContextoDefiniciones(
        grupos_cuotas_cantidades={},
        billeteras_inversion_ids=set(),
        categoria_ahorro_ids=set(),
        subcategoria_tarjeta_id=None,
        subcategoria_tarjeta_ids=set(),
        hoy=hoy,
    )


def test_regular_fijo():
    """Regla: 1 cobro por ciclo y CV <= 0.10 -> Regular por el último monto."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    txs = []
    for ini, fin in ciclos:
        txs.append(MockTransaccion(fecha=ini, monto=1000000, categoria="Empleo", subcategoria="Sueldo"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "regular"
    assert hab_ars.monto == Decimal("1000000.00")
    assert hab_ars.motivo_sin_datos is None
    assert len(hab_ars.fuentes) == 1


def test_regular_con_aumentos_toma_ultimo():
    """Regla: Regular con aumentos toma el último cobro, contando el ciclo actual."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    # Ciclo actual: julio 2026
    ciclo_actual = (date(2026, 7, 1), date(2026, 7, 31))
    hoy = date(2026, 7, 15)
    ctx = _ctx(hoy)

    # 6 ciclos completos con aumentos moderados (CV <= 0.10)
    montos = [1000000, 1000000, 1050000, 1050000, 1100000, 1100000]
    txs = []
    for (ini, fin), m in zip(ciclos, montos):
        txs.append(MockTransaccion(fecha=ini, monto=m, categoria="Empleo", subcategoria="Sueldo"))

    # Cobro del ciclo actual con nuevo aumento a 1.200.000
    txs.append(MockTransaccion(fecha=date(2026, 7, 5), monto=1200000, categoria="Empleo", subcategoria="Sueldo"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos + [ciclo_actual], hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "regular"
    assert hab_ars.monto == Decimal("1200000.00")


def test_intermitente():
    """Regla: varios cobros por ciclo con total estable (CV <= 0.15) -> Intermitente (mediana de totales)."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    txs = []
    for ini, fin in ciclos:
        # 3 cobros por ciclo de 250.000 cada uno (total 750.000)
        txs.append(MockTransaccion(fecha=ini, monto=250000, categoria="Freelance", subcategoria="Proyectos"))
        txs.append(MockTransaccion(fecha=ini, monto=250000, categoria="Freelance", subcategoria="Proyectos"))
        txs.append(MockTransaccion(fecha=ini, monto=250000, categoria="Freelance", subcategoria="Proyectos"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "intermitente"
    assert hab_ars.monto == Decimal("750000.00")


def test_variable_tres_mas_flojos():
    """Regla: Variable toma el promedio de los 3 totales más bajos de los últimos 12 ciclos."""
    ciclos = _generar_ciclos_mensuales(2025, 7, 12)
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    # 12 totales muy variables (CV > 0.15)
    # Los 3 más bajos: 800.000, 850.000, 900.000 -> promedio = 850.000
    totales = [1500000, 2000000, 800000, 1700000, 900000, 1400000, 1800000, 850000, 1600000, 1900000, 1300000, 1500000]
    txs = []
    for (ini, fin), tot in zip(ciclos, totales):
        txs.append(MockTransaccion(fecha=ini, monto=tot, categoria="Honorarios", subcategoria="Consultoría"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "variable"
    assert hab_ars.monto == Decimal("850000.00")


def test_variable_menos_de_cuatro_ciclos():
    """Regla: Variable con menos de 4 ciclos toma el total más bajo."""
    ciclos = _generar_ciclos_mensuales(2026, 4, 3)
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    # 3 ciclos variables: 1.200.000, 800.000, 1.500.000 -> más bajo = 800.000
    totales = [1200000, 800000, 1500000]
    txs = []
    for (ini, fin), tot in zip(ciclos, totales):
        txs.append(MockTransaccion(fecha=ini, monto=tot, categoria="Ventas", subcategoria="Comisiones"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "variable"
    assert hab_ars.monto == Decimal("800000.00")


def test_separacion_jubilacion_mas_bono():
    """Regla: Si 2 o más cobros por ciclo y el 2do cobro es < 50% del primero en mediana, se desdobla en 2 series."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    txs = []
    for ini, fin in ciclos:
        # Cobro mayor (haber): 400.000
        txs.append(MockTransaccion(fecha=ini, monto=400000, categoria="Jubilación", subcategoria="General"))
        # Cobro menor (bono): 70.000 (< 50% de 400.000)
        txs.append(MockTransaccion(fecha=ini, monto=70000, categoria="Jubilación", subcategoria="General"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "regular"
    # La suma de ambas fuentes desdobladas: 400.000 + 70.000 = 470.000
    assert hab_ars.monto == Decimal("470000.00")
    assert len(hab_ars.fuentes) == 2
    assert all(f.es_desdoblada for f in hab_ars.fuentes)


def test_aguinaldo_solo_con_historia():
    """Regla: Aguinaldo solo para quien cobró un aguinaldo en los últimos 12 meses."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    # Caso 1: Sueldo regular SIN aguinaldo previo
    txs_sin_ag = [
        MockTransaccion(fecha=ini, monto=1000000, categoria="Empleo", subcategoria="Sueldo")
        for ini, fin in ciclos
    ]
    res1 = calcular_ingreso_habitual_en_memoria(txs_sin_ag, ciclos, hoy, ctx)
    assert res1["ars"].tiene_aguinaldo is False
    assert res1["ars"].proximo_aguinaldo_fecha is None
    assert res1["ars"].proximo_aguinaldo_monto is None

    # Caso 2: Se agrega cobro previo de aguinaldo en los últimos 12 meses
    txs_con_ag = list(txs_sin_ag)
    txs_con_ag.append(MockTransaccion(fecha=date(2025, 12, 18), monto=500000, categoria="Empleo", subcategoria="Aguinaldo"))
    res2 = calcular_ingreso_habitual_en_memoria(txs_con_ag, ciclos, hoy, ctx)
    assert res2["ars"].tiene_aguinaldo is True
    assert res2["ars"].proximo_aguinaldo_monto == Decimal("500000.00")


def test_fecha_aguinaldo_copiada_del_ultimo():
    """Regla: Fecha esperada de aguinaldo copia día y mes del último cobrado para ese semestre."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    # Evaluamos en agosto 2026 (segundo semestre)
    hoy = date(2026, 8, 10)
    ctx = _ctx(hoy)

    txs = [
        MockTransaccion(fecha=ini, monto=2000000, categoria="Empleo", subcategoria="Sueldo")
        for ini, fin in ciclos
    ]
    # Último aguinaldo de diciembre cobrado el 18/12/2025
    txs.append(MockTransaccion(fecha=date(2025, 12, 18), monto=900000, categoria="Empleo", subcategoria="Aguinaldo"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tiene_aguinaldo is True
    assert hab_ars.proximo_aguinaldo_fecha == date(2026, 12, 18)
    assert hab_ars.proximo_aguinaldo_monto == Decimal("1000000.00")  # 50% de 2.000.000


def test_sin_datos_por_pocos_ciclos():
    """Regla: Hacen falta mínimo 3 ciclos completos; con menos, queda sin datos con motivo pocos_ciclos."""
    ciclos = _generar_ciclos_mensuales(2026, 5, 2)  # solo 2 ciclos
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    txs = [
        MockTransaccion(fecha=ini, monto=1000000, categoria="Empleo", subcategoria="Sueldo")
        for ini, fin in ciclos
    ]

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "sin_datos"
    assert hab_ars.monto is None
    assert hab_ars.motivo_sin_datos == "pocos_ciclos"


def test_sin_datos_por_irregular():
    """Regla: Si una fuente aparece en menos del 80% de los ciclos completos, es irregular."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)  # 6 ciclos completos
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    # Aparece en solo 2 de los 6 ciclos (33% < 80%)
    txs = [
        MockTransaccion(fecha=ciclos[0][0], monto=1000000, categoria="Freelance", subcategoria="Extras"),
        MockTransaccion(fecha=ciclos[2][0], monto=1000000, categoria="Freelance", subcategoria="Extras"),
    ]

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    hab_ars = res["ars"]
    assert hab_ars.tipo == "sin_datos"
    assert hab_ars.monto is None
    assert hab_ars.motivo_sin_datos == "irregular"


def test_ingreso_esperado_ciclo_ya_cobrado():
    """Regla: Ingreso esperado suma lo ya cobrado + pendiente de cada fuente. Si ya cobró todo, es lo ya cobrado."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    ciclo_act = (date(2026, 7, 1), date(2026, 7, 31))
    hoy = date(2026, 7, 15)
    ctx = _ctx(hoy)

    txs = [
        MockTransaccion(fecha=ini, monto=1000000, categoria="Empleo", subcategoria="Sueldo")
        for ini, fin in ciclos
    ]
    # En el ciclo actual ya cobró sueldo de 1.000.000 + extra de 200.000
    txs.append(MockTransaccion(fecha=date(2026, 7, 5), monto=1000000, categoria="Empleo", subcategoria="Sueldo"))
    txs.append(MockTransaccion(fecha=date(2026, 7, 10), monto=200000, categoria="Otros", subcategoria="Extra"))

    hab = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    esp = calcular_ingreso_esperado_ciclo_en_memoria(
        movimientos_ciclo=[t for t in txs if ciclo_act[0] <= t.fecha <= ciclo_act[1]],
        fecha_inicio=ciclo_act[0],
        fecha_fin=ciclo_act[1],
        ingreso_habitual=hab["ars"],
        hoy=hoy,
        ctx=ctx,
    )
    # Ya cobró 1.200.000. Pendiente de sueldo = max(0, 1.000.000 - 1.000.000) = 0. Total = 1.200.000
    assert esp == Decimal("1200000.00")


def test_ingreso_esperado_ciclo_no_cobrado():
    """Regla: Ingreso esperado cuando aún no se cobró nada en el ciclo actual -> monto de fuentes habituales."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    ciclo_act = (date(2026, 7, 1), date(2026, 7, 31))
    hoy = date(2026, 7, 2)
    ctx = _ctx(hoy)

    txs = [
        MockTransaccion(fecha=ini, monto=1000000, categoria="Empleo", subcategoria="Sueldo")
        for ini, fin in ciclos
    ]
    # Nada cobrado aún en el ciclo actual

    hab = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)
    esp = calcular_ingreso_esperado_ciclo_en_memoria(
        movimientos_ciclo=[],
        fecha_inicio=ciclo_act[0],
        fecha_fin=ciclo_act[1],
        ingreso_habitual=hab["ars"],
        hoy=hoy,
        ctx=ctx,
    )
    assert esp == Decimal("1000000.00")


def test_usd_separado_de_ars():
    """Regla: ARS y USD se calculan de manera totalmente independiente y separada."""
    ciclos = _generar_ciclos_mensuales(2026, 1, 6)
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    txs = []
    # ARS: Sueldo regular de 1.500.000 en los 6 ciclos
    for ini, fin in ciclos:
        txs.append(MockTransaccion(fecha=ini, monto=1500000, moneda="ARS", categoria="Empleo", subcategoria="Sueldo"))

    # USD: Solo un cobro suelto en 1 ciclo
    txs.append(MockTransaccion(fecha=ciclos[2][0], monto=500, moneda="USD", categoria="Freelance", subcategoria="Exterior"))

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx)

    # ARS regular
    assert res["ars"].tipo == "regular"
    assert res["ars"].monto == Decimal("1500000.00")

    # USD sin datos (1 cobro en 4 ciclos es < 80% -> irregular)
    assert res["usd"].tipo == "sin_datos"
    assert res["usd"].monto is None
    assert res["usd"].motivo_sin_datos == "irregular"
