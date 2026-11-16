# -*- coding: utf-8 -*-
"""
app/services/permitirse_evaluacion.py — Funciones puras de "¿Me lo puedo permitir?" (bloque T1).

Acá no se lee la base: todo entra por parámetros y sale como valores o diccionarios.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from app.services.fin_de_mes_service import redondear_mil

CERO = Decimal("0.00")
CENTAVO = Decimal("0.01")

# Orden de los cuatro resultados, del mejor al peor (para comparar).
ORDEN_RESULTADOS = {"bien": 3, "justo": 2, "ahorros": 1, "no_alcanza": 0}


def _dec(valor) -> Decimal:
    """Pasa cualquier número a Decimal sin arrastrar errores de float."""
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def sobrante_prudente(netos: list[Decimal]) -> Decimal | None:
    """
    Lo que le sobra a la persona por mes, con prudencia una sola vez (decisión 4A):
    el promedio de los 3 ciclos en que menos le sobró. Con menos de 3 ciclos devuelve None.
    """
    if len(netos) < 3:
        return None
    mas_flojos = sorted(_dec(n) for n in netos)[:3]
    promedio = sum(mas_flojos, Decimal("0")) / Decimal("3")
    return promedio.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def plata_de_cuentas(
    finales: tuple[Decimal, ...] | list[Decimal] | None,
    libre: Decimal,
    dolares_en_pesos: Decimal = CERO,
) -> Decimal:
    """
    Plata de las cuentas comunes que se puede usar sin tocar ahorros (D-T1.2).
    Con finales de fin de mes: el más bajo; si no hay: lo libre. Nunca negativa.
    Los dólares libres ya pasados a pesos se suman solo si la persona usa dólares seguido
    (quien llama decide si los pasa).
    """
    if finales:
        base = max(CERO, min(_dec(f) for f in finales))
    else:
        base = max(CERO, _dec(libre))
    return (base + max(CERO, _dec(dolares_en_pesos))).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def _margen(margenes: list[Decimal], k: int) -> Decimal:
    """Margen del ciclo k; pasado el horizonte se repite el último conocido."""
    if not margenes:
        return CERO
    return _dec(margenes[k]) if k < len(margenes) else _dec(margenes[-1])


def evaluar_cuotas_cuentas(
    cuotas: list[Decimal],
    margenes: list[Decimal],
    colchon: Decimal | None,
    B: Decimal,
    ahorros: Decimal,
) -> dict:
    """
    Evalúa una compra en cuotas contando la plata de las cuentas como respaldo (D-T1.3).
    Devuelve resultado, r_min, deficit, meses_deficit, uso_mensual, falta y usa_cuentas.
    """
    n = len(cuotas)
    cuotas_d = [_dec(c) for c in cuotas]
    r = [_margen(margenes, k) - cuotas_d[k] for k in range(n)]
    r_min = min(r) if r else CERO
    salida = {
        "resultado": "justo",
        "r_min": r_min,
        "deficit": CERO,
        "meses_deficit": 0,
        "uso_mensual": CERO,
        "falta": CERO,
        "usa_cuentas": False,
    }

    # a) Sobra con colchón todos los meses.
    if colchon is not None and all(x >= _dec(colchon) for x in r):
        salida["resultado"] = "bien"
        return salida
    # b) Entra todos los meses sin usar las cuentas.
    if all(x >= CERO for x in r):
        return salida

    # c) Solo se le carga a la compra lo que pone la compra (no lo que ya pierde cada mes).
    faltantes = [cuotas_d[k] - max(CERO, _margen(margenes, k)) for k in range(n)]
    deficit = sum((max(CERO, f) for f in faltantes), CERO)
    meses_deficit = sum(1 for f in faltantes if f > CERO)
    salida["deficit"] = deficit.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    salida["meses_deficit"] = meses_deficit

    plata = _dec(B)
    ahorros_d = _dec(ahorros)
    if deficit <= plata:
        salida["resultado"] = "justo"
        salida["usa_cuentas"] = True
        if meses_deficit > 0:
            salida["uso_mensual"] = redondear_mil(deficit / Decimal(meses_deficit))
    elif deficit <= plata + ahorros_d:
        salida["resultado"] = "ahorros"
        salida["falta"] = (deficit - plata).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    else:
        salida["resultado"] = "no_alcanza"
        salida["falta"] = (deficit - plata - ahorros_d).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    return salida


def evaluar_mensual_cuentas(
    precio: Decimal,
    margenes: list[Decimal],
    colchon: Decimal | None,
    B: Decimal,
    ahorros: Decimal,
) -> dict:
    """
    Evalúa un gasto de todos los meses contando la plata de las cuentas (D-T1.3).
    Devuelve resultado, r_min, deficit_mensual, meses_cuentas, meses_total y usa_cuentas.
    """
    precio_d = _dec(precio)
    r = [_dec(m) - precio_d for m in margenes]
    r_min = min(r) if r else CERO
    salida = {
        "resultado": "justo",
        "r_min": r_min,
        "deficit_mensual": CERO,
        "meses_cuentas": 0,
        "meses_total": 0,
        "usa_cuentas": False,
    }

    if colchon is not None and r and all(x >= _dec(colchon) for x in r):
        salida["resultado"] = "bien"
        return salida
    if all(x >= CERO for x in r):
        return salida

    # c) Lo que la compra pone de más por mes en los primeros 12 ciclos.
    primeros = [precio_d - max(CERO, _dec(m)) for m in margenes[:12]]
    deficit_mensual = max((max(CERO, f) for f in primeros), default=CERO)
    if deficit_mensual <= CERO:
        # En los próximos 12 meses entra sin usar las cuentas: queda justo, sin plazo.
        salida["r_min"] = min(r[:12]) if r else CERO
        return salida

    plata = _dec(B)
    total = plata + _dec(ahorros)
    meses_cuentas = int((plata / deficit_mensual).to_integral_value(rounding=ROUND_FLOOR))
    meses_total = int((total / deficit_mensual).to_integral_value(rounding=ROUND_FLOOR))
    salida["deficit_mensual"] = deficit_mensual.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    salida["meses_cuentas"] = meses_cuentas
    salida["meses_total"] = meses_total
    salida["usa_cuentas"] = True
    if meses_cuentas >= 12:
        salida["resultado"] = "justo"
    elif meses_total >= 12:
        salida["resultado"] = "ahorros"
    else:
        salida["resultado"] = "no_alcanza"
    return salida


def piso_por_contado(resultado_cuotas: str, resultado_contado: str, tiene_interes: bool) -> str:
    """
    Pagar lo mismo más tarde nunca puede ser más difícil: en cuotas sin interés, si de contado
    entra (bien o justo), las cuotas no pueden dar "ahorros" ni "no_alcanza".
    """
    if tiene_interes:
        return resultado_cuotas
    if resultado_contado in ("bien", "justo") and resultado_cuotas in ("ahorros", "no_alcanza"):
        return "justo"
    return resultado_cuotas


def meses_para_juntar(falta_bruta: Decimal, sobrante: Decimal | None) -> int | None:
    """
    En cuántos meses junta lo que le falta con lo que le sobra por mes (D-T1d).
    Devuelve None si no le sobra nada, si no falta nada o si son más de 36 meses.
    """
    if sobrante is None or _dec(sobrante) <= CERO or _dec(falta_bruta) <= CERO:
        return None
    meses = int((_dec(falta_bruta) / _dec(sobrante)).to_integral_value(rounding=ROUND_CEILING))
    if 1 <= meses <= 36:
        return meses
    return None
