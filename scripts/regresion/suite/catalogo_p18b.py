# -*- coding: utf-8 -*-
"""
scripts/regresion/suite/catalogo_p18b.py

Entradas de catálogo para los escenarios P18B.1 a P18B.4 de la suite de WhatsApp.
"""
from __future__ import annotations

from typing import Any

from scripts.regresion.suite.escenarios_p18b import (
    p18b_caso_1,
    p18b_caso_2,
    p18b_caso_3,
    p18b_caso_4,
)


def entradas_p18b(datos: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna las entradas de catálogo para los escenarios P18B.1 a P18B.4."""
    return [
        {
            "id": "P18B.1",
            "punto": "Punto 18B",
            "match": "contiene",
            "nombre": "me da para una tele de 300 mil? determinístico",
            "ejecutar": lambda: p18b_caso_1(datos),
            "esperado": "Intent: puede_permitirse",
        },
        {
            "id": "P18B.2",
            "punto": "Punto 18B",
            "match": "contiene",
            "nombre": "¿me lo puedo permitir? pregunta precio y 300 mil completa",
            "ejecutar": lambda: p18b_caso_2(datos),
            "esperado": "Intent2: puede_permitirse | Llegó a la IA: False",
        },
        {
            "id": "P18B.3",
            "punto": "Punto 18B",
            "match": "contiene",
            "nombre": "me puedo permitir un viaje de 1000 dólares? cotiza dólar oficial",
            "ejecutar": lambda: p18b_caso_3(datos),
            "esperado": "Lo calculé con el dólar oficial de hoy",
        },
        {
            "id": "P18B.4",
            "punto": "Punto 18B",
            "match": "contiene",
            "nombre": "me puedo permitir pagar el gimnasio de 40 mil por mes?",
            "ejecutar": lambda: p18b_caso_4(datos),
            "esperado": "Intent: puede_permitirse",
        },
    ]
