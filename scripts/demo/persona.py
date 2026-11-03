# -*- coding: utf-8 -*-
"""
Plan de datos de la persona inventada (Lucía Demo).
Módulo puramente declarativo de datos, sin interacción con la base ni imports pesados.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, NamedTuple

EMAIL_DEMO = "demo.argentum@argentum.com"
NOMBRE_DEMO = "Lucía"
APELLIDO_DEMO = "Demo"
EDAD_DEMO = 29
PROFESION_DEMO = "Diseñadora en relación de dependencia"
UBICACION_DEMO = "CABA"
SEMILLA_DEMO = 20260525

FECHA_INICIO = date(2026, 5, 25)
FECHA_FIN_FUTURO = date(2027, 8, 21)


class PlanGastoVariable(NamedTuple):
    fecha: date
    monto: Decimal
    descripcion: str
    billetera_clave: str
    es_credito: bool
    categoria_nombre: str
    subcategoria_nombre: str
    es_reintegro_25: bool = False
    reintegro_monto: Decimal | None = None
    reintegro_fecha: date | None = None


def construir_plan_completo(seed: int = SEMILLA_DEMO) -> Dict[str, Any]:
    """
    Construye de forma puramente determinística todas las fechas, montos y movimientos
    planificados para Lucía Demo desde el 25/05/2026 hasta el 21/08/2027.
    """
    rng = random.Random(seed)

    # 1. Sueldos mensuales (día 25)
    # $2.600.000 inicial; +4% el 25/08/2026, 25/11/2026, 25/02/2027, 25/05/2027
    sueldos: List[Dict[str, Any]] = []
    meses_sueldo = [
        (date(2026, 5, 25), Decimal("2600000.00")),
        (date(2026, 6, 25), Decimal("2600000.00")),
        (date(2026, 7, 25), Decimal("2600000.00")),
        (date(2026, 8, 25), Decimal("2704000.00")),
        (date(2026, 9, 25), Decimal("2704000.00")),
        (date(2026, 10, 25), Decimal("2704000.00")),
        (date(2026, 11, 25), Decimal("2812000.00")),
        (date(2026, 12, 25), Decimal("2812000.00")),
        (date(2027, 1, 25), Decimal("2812000.00")),
        (date(2027, 2, 25), Decimal("2924000.00")),
        (date(2027, 3, 25), Decimal("2924000.00")),
        (date(2027, 4, 25), Decimal("2924000.00")),
        (date(2027, 5, 25), Decimal("3041000.00")),
        (date(2027, 6, 25), Decimal("3041000.00")),
        (date(2027, 7, 25), Decimal("3041000.00")),
    ]
    for f, m in meses_sueldo:
        sueldos.append({
            "fecha": f,
            "monto": m,
            "descripcion": f"Sueldo neto {f.strftime('%m/%Y')}",
        })

    # Aguinaldos
    aguinaldos = [
        {"fecha": date(2026, 6, 30), "monto": Decimal("1300000.00"), "descripcion": "Sueldo Anual Complementario (SAC 1)"},
        {"fecha": date(2026, 12, 18), "monto": Decimal("1406000.00"), "descripcion": "Sueldo Anual Complementario (SAC 2)"},
        {"fecha": date(2027, 6, 30), "monto": Decimal("1520500.00"), "descripcion": "Sueldo Anual Complementario (SAC 1)"},
    ]

    # 2. Fijos (Galicia débito)
    # Alquiler día 1
    alquileres: List[Dict[str, Any]] = []
    fechas_alquiler = [
        (date(2026, 6, 1), Decimal("620000.00")),
        (date(2026, 7, 1), Decimal("620000.00")),
        (date(2026, 8, 1), Decimal("620000.00")),
        (date(2026, 9, 1), Decimal("620000.00")),
        (date(2026, 10, 1), Decimal("620000.00")),
        (date(2026, 11, 1), Decimal("620000.00")),
        (date(2026, 12, 1), Decimal("713000.00")),
        (date(2027, 1, 1), Decimal("713000.00")),
        (date(2027, 2, 1), Decimal("713000.00")),
        (date(2027, 3, 1), Decimal("713000.00")),
        (date(2027, 4, 1), Decimal("713000.00")),
        (date(2027, 5, 1), Decimal("713000.00")),
        (date(2027, 6, 1), Decimal("798560.00")),
        (date(2027, 7, 1), Decimal("798560.00")),
        (date(2027, 8, 1), Decimal("798560.00")),
    ]
    for f, m in fechas_alquiler:
        alquileres.append({"fecha": f, "monto": m, "descripcion": f"Alquiler departamento {f.strftime('%m/%Y')}"})

    # Expensas día 10 (+3% por mes)
    expensas: List[Dict[str, Any]] = []
    monto_exp = Decimal("85000.00")
    for year, month in [
        (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
        (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6), (2027, 7), (2027, 8)
    ]:
        f = date(year, month, 10)
        expensas.append({"fecha": f, "monto": monto_exp.quantize(Decimal("1.00")), "descripcion": f"Expensas edificio {f.strftime('%m/%Y')}"})
        monto_exp = (monto_exp * Decimal("1.03")).quantize(Decimal("0.01"))

    # Luz (día 15 meses pares: entre $24.000 y $38.000)
    luz_list: List[Dict[str, Any]] = []
    for year, month in [(2026, 6), (2026, 8), (2026, 10), (2026, 12), (2027, 2), (2027, 4), (2027, 6), (2027, 8)]:
        f = date(year, month, 15)
        m = Decimal(rng.randint(24000, 38000))
        luz_list.append({"fecha": f, "monto": m, "descripcion": f"Edenor {f.strftime('%m/%Y')}"})

    # Gas (día 18 meses impares: entre $9.000 y $26.000, más en invierno)
    gas_list: List[Dict[str, Any]] = []
    for year, month in [(2026, 7), (2026, 9), (2026, 11), (2027, 1), (2027, 3), (2027, 5), (2027, 7)]:
        f = date(year, month, 18)
        if month in (7, 9, 5):
            m = Decimal(rng.randint(19000, 26000))
        else:
            m = Decimal(rng.randint(9000, 16000))
        gas_list.append({"fecha": f, "monto": m, "descripcion": f"Metrogas {f.strftime('%m/%Y')}"})

    # Agua (día 12 de cada mes: entre $11.000 y $13.000)
    agua_list: List[Dict[str, Any]] = []
    for year, month in [
        (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
        (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6), (2027, 7), (2027, 8)
    ]:
        f = date(year, month, 12)
        m = Decimal(rng.randint(11000, 13000))
        agua_list.append({"fecha": f, "monto": m, "descripcion": f"AySA {f.strftime('%m/%Y')}"})

    # 3. Cuotas fijas con Visa Galicia
    cuotas_plan = [
        {
            "fecha": date(2026, 6, 15),
            "descripcion": "Heladera Samsung No Frost",
            "monto_total": Decimal("1200000.00"),
            "cantidad_cuotas": 12,
            "tiene_interes": False,
            "tasa_interes": None,
        },
        {
            "fecha": date(2026, 9, 10),
            "descripcion": "Zapatillas Nike Running",
            "monto_total": Decimal("180000.00"),
            "cantidad_cuotas": 3,
            "tiene_interes": False,
            "tasa_interes": None,
        },
        {
            "fecha": date(2027, 1, 20),
            "descripcion": "Celular iPhone 14",
            "monto_total": Decimal("1500000.00"),
            "cantidad_cuotas": 18,
            "tiene_interes": True,
            "tasa_interes": Decimal("80.00"),
        },
        {
            "fecha": date(2027, 4, 5),
            "descripcion": "Notebook Dell Inspiron",
            "monto_total": Decimal("1900000.00"),
            "cantidad_cuotas": 12,
            "tiene_interes": False,
            "tasa_interes": None,
        },
    ]

    # 4. Dólares
    # Viaje a Brasil del 08/01/2027 al 18/01/2027: 8 gastos en dólares desde Dólares (US$600 total)
    gastos_brasil = [
        {"fecha": date(2027, 1, 8), "monto": Decimal("90.00"), "descripcion": "Cena Copacabana"},
        {"fecha": date(2027, 1, 9), "monto": Decimal("60.00"), "descripcion": "Entrada Cristo Redentor"},
        {"fecha": date(2027, 1, 10), "monto": Decimal("75.00"), "descripcion": "Almuerzo Ipanema"},
        {"fecha": date(2027, 1, 12), "monto": Decimal("110.00"), "descripcion": "Paseo en barco Angra"},
        {"fecha": date(2027, 1, 13), "monto": Decimal("45.00"), "descripcion": "Snacks y playa"},
        {"fecha": date(2027, 1, 15), "monto": Decimal("80.00"), "descripcion": "Cena Leblon"},
        {"fecha": date(2027, 1, 16), "monto": Decimal("90.00"), "descripcion": "Compras souvenirs Rio"},
        {"fecha": date(2027, 1, 17), "monto": Decimal("50.00"), "descripcion": "Transporte y cafés"},
    ]

    # 5. Variables semanales generadas determinísticamente
    # Súper, Delivery, Café, SUBE, Salidas, Farmacia, Ropa, Regalo
    variables: List[PlanGastoVariable] = []

    cur = FECHA_INICIO
    lunes_del_mes_count: Dict[str, int] = {}

    while cur <= FECHA_FIN_FUTURO:
        ym = cur.strftime("%Y-%m")
        # SUBE semanal los lunes ($10.000) - a partir de que la billetera tiene fondos (> FECHA_INICIO)
        if cur.weekday() == 0:  # Lunes
            if cur > FECHA_INICIO:
                variables.append(PlanGastoVariable(
                    fecha=cur,
                    monto=Decimal("10000.00"),
                    descripcion="Recarga SUBE",
                    billetera_clave="mercado_pago",
                    es_credito=False,
                    categoria_nombre="Transporte",
                    subcategoria_nombre="Transporte público"
                ))

            # Súper los lunes: 2 súper por mes llevan reintegro del 25% con Visa Galicia
            lunes_count = lunes_del_mes_count.get(ym, 0) + 1
            lunes_del_mes_count[ym] = lunes_count

            es_reintegro = (lunes_count <= 2)
            m_super = Decimal(rng.randint(35000, 68000))
            r_monto = (m_super * Decimal("0.25")).quantize(Decimal("1.00")) if es_reintegro else None
            r_fecha = cur + timedelta(days=3) if es_reintegro else None

            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=m_super,
                descripcion="Supermercado Coto",
                billetera_clave="galicia",
                es_credito=True,
                categoria_nombre="Comida",
                subcategoria_nombre="Supermercado",
                es_reintegro_25=es_reintegro,
                reintegro_monto=r_monto,
                reintegro_fecha=r_fecha
            ))

        # Súper jueves (mitad débito MP)
        if cur.weekday() == 3:  # Jueves
            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=Decimal(rng.randint(25000, 50000)),
                descripcion="Supermercado Día",
                billetera_clave="mercado_pago",
                es_credito=False,
                categoria_nombre="Comida",
                subcategoria_nombre="Supermercado"
            ))

        # Delivery (viernes)
        if cur.weekday() == 4:  # Viernes
            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=Decimal(rng.randint(15000, 30000)),
                descripcion="Delivery PedidosYa",
                billetera_clave="mercado_pago",
                es_credito=False,
                categoria_nombre="Comida",
                subcategoria_nombre="Delivery"
            ))

        # Café (martes, miércoles, viernes con Efectivo)
        if cur.weekday() in (1, 2, 4):
            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=Decimal(rng.randint(4000, 6500)),
                descripcion="Café al paso",
                billetera_clave="efectivo",
                es_credito=False,
                categoria_nombre="Comida",
                subcategoria_nombre="Cafetería"
            ))

        # Salidas fines de semana (sábado con Visa Galicia)
        if cur.weekday() == 5:  # Sábado
            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=Decimal(rng.randint(25000, 60000)),
                descripcion="Salida bar / restaurante",
                billetera_clave="galicia",
                es_credito=True,
                categoria_nombre="Ocio",
                subcategoria_nombre="Salidas"
            ))

        # Farmacia día 14
        if cur.day == 14:
            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=Decimal(rng.randint(8000, 30000)),
                descripcion="Farmacity medicamentos",
                billetera_clave="galicia",
                es_credito=False,
                categoria_nombre="Salud",
                subcategoria_nombre="Farmacia"
            ))

        # Ropa día 19 (Visa Galicia)
        if cur.day == 19:
            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=Decimal(rng.randint(40000, 120000)),
                descripcion="Ropa Zara",
                billetera_clave="galicia",
                es_credito=True,
                categoria_nombre="Compras",
                subcategoria_nombre="Ropa"
            ))

        # Regalo día 7 de meses pares (Galicia débito)
        if cur.day == 7 and cur.month % 2 == 0:
            variables.append(PlanGastoVariable(
                fecha=cur,
                monto=Decimal(rng.randint(20000, 50000)),
                descripcion="Regalo cumpleaños",
                billetera_clave="galicia",
                es_credito=False,
                categoria_nombre="Compras",
                subcategoria_nombre="Regalos"
            ))

        cur += timedelta(days=1)

    return {
        "sueldos": sueldos,
        "aguinaldos": aguinaldos,
        "alquileres": alquileres,
        "expensas": expensas,
        "luz": luz_list,
        "gas": gas_list,
        "agua": agua_list,
        "cuotas_plan": cuotas_plan,
        "gastos_brasil": gastos_brasil,
        "variables": variables,
    }

