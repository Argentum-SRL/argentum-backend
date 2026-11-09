# -*- coding: utf-8 -*-
"""
app/services/presupuesto_sugerencias_reglas.py
Funciones puras y constantes para sugerencias y ritmo de presupuestos.
Sin acceso a base de datos, sin red, sin hoy_argentina.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.consejos_reglas import m, redondeo, redondeo_arriba
from app.utils.finanzas import mediana

SUBCATEGORIAS_FIJAS: frozenset[tuple[str, str]] = frozenset({
    ("Vivienda", "Alquiler"),
    ("Vivienda", "Expensas"),
    ("Vivienda", "Luz"),
    ("Vivienda", "Gas"),
    ("Vivienda", "Agua"),
    ("Salud", "Prepaga y obra social"),
    ("Educación", "Colegio y facultad"),
    ("Tecnología", "Celular"),
    ("Tecnología", "Internet"),
    ("Tecnología", "Apps y programas"),
    ("Ocio", "Streaming"),
    ("Ocio", "Deportes y gimnasio"),
    ("Impuestos", "ABL e inmobiliario"),
    ("Impuestos", "Patente"),
    ("Impuestos", "Monotributo"),
    ("Banco", "Préstamos"),
    ("Banco", "Seguros"),
    ("Hogar", "Servicio doméstico"),
})


def proyectar_ritmo(
    gastado: Decimal,
    fijo: Decimal,
    pendiente: Decimal,
    inicio: date,
    fin: date,
    hoy: date,
    limite: Decimal,
    moneda: str = "ARS",
) -> Decimal | None:
    """
    Proyecta el gasto a fin de período para un presupuesto activo.
    Solo proyecta la parte variable; lo fijo se suma tal cual.
    """
    if limite <= Decimal("0") or hoy < inicio or hoy > fin:
        return None

    total = (fin - inicio).days + 1
    transcurridos = (hoy - inicio).days + 1

    if transcurridos * 10 < total * 3:
        return None

    if gastado > limite:
        return None

    variable = max(gastado - fijo, Decimal("0"))
    proyeccion = fijo + pendiente + (variable * Decimal(str(total))) / Decimal(str(transcurridos))

    if redondeo(proyeccion, moneda) <= limite:
        return None

    return proyeccion


def linea_ritmo(proyeccion: Decimal, limite: Decimal, moneda: str = "ARS") -> str:
    """Devuelve la frase resumen de proyección de ritmo de gasto."""
    return f"A este ritmo llegás a unos {m(redondeo(proyeccion, moneda), moneda)} (límite {m(limite, moneda)})."


@dataclass(frozen=True)
class SugerenciaLimite:
    texto: str
    monto: Decimal
    boton: str


def sugerir_limite(
    periodo: str,
    limite_actual: Decimal,
    calculado_actual: Decimal,
    compras_actual: int,
    previos: list[tuple[Decimal, Decimal, int]],
    moneda: str = "ARS",
) -> SugerenciaLimite | None:
    """
    Evalúa si corresponde sugerir una subida de límite basada en escalón o en 3 períodos superados.
    """
    if len(previos) < 3:
        return None

    previos3 = previos[:3]

    # 2. Escalón (solo mensual)
    if (
        periodo == "mensual"
        and all(p[2] == 1 for p in previos3)
        and compras_actual == 1
        and calculado_actual > limite_actual
        and calculado_actual > max(p[0] for p in previos3)
    ):
        x = redondeo_arriba(calculado_actual, moneda)
        texto = f"Este mes el pago subió a {m(calculado_actual, moneda)}, más que tu límite de {m(limite_actual, moneda)}."
        boton = f"Subir a {m(x, moneda)}"
        return SugerenciaLimite(texto=texto, monto=x, boton=boton)

    # 3. Tres períodos consecutivos superados
    if all(p[0] > p[1] for p in previos3):
        calculados = [p[0] for p in previos3]
        med = mediana(calculados)
        x = redondeo_arriba(med, moneda)
        if x <= limite_actual:
            return None

        frase = (
            "los últimos 3 meses"
            if periodo == "mensual"
            else ("las últimas 3 quincenas" if periodo == "quincenal" else "las últimas 3 semanas")
        )
        texto = f"Te pasaste {frase}. Lo normal tuyo es {m(x, moneda)}."
        boton = f"Subir a {m(x, moneda)}"
        return SugerenciaLimite(texto=texto, monto=x, boton=boton)

    return None

