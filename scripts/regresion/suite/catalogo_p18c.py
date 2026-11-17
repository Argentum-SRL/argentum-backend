# -*- coding: utf-8 -*-
"""
scripts/regresion/suite/catalogo_p18c.py

Entradas de catálogo para los escenarios P18C.1 y P18C.2 de la suite de WhatsApp.
"""
from __future__ import annotations

from typing import Any

from scripts.regresion.suite.escenarios_p18c import p18c_caso_1, p18c_caso_2


def entradas_p18c(datos: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna las entradas de catálogo para los escenarios P18C.1 y P18C.2."""
    return [
        {
            "id": "P18C.1",
            "punto": "Punto 18C",
            "match": "contiene",
            "nombre": "¿me conviene 500 mil contado o 12 de 50 mil? determinístico",
            "ejecutar": lambda: p18c_caso_1(datos),
            "esperado": "Intent: cuotas_vs_contado",
        },
        {
            "id": "P18C.2",
            "punto": "Punto 18C",
            "match": "contiene",
            "nombre": "compra en cuotas con precio de contado por WhatsApp",
            "ejecutar": lambda: p18c_caso_2(datos),
            "esperado": "Precio de contado guardado: True",
        },
    ]
