# -*- coding: utf-8 -*-
"""
scripts/regresion/suite/catalogo_p09c.py

Entradas de catálogo para los escenarios P9C.1 a P9C.6 de la suite de regresión de WhatsApp.
"""
from __future__ import annotations

from typing import Any

from scripts.regresion.suite.escenarios_p09c import (
    p9c_caso_1,
    p9c_caso_2,
    p9c_caso_3,
    p9c_caso_4,
    p9c_caso_5,
    p9c_caso_6,
)


def entradas_p09c(datos: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna las entradas de catálogo para los escenarios P9C.1 a P9C.6."""
    return [
        {
            "id": "P9C.1", "punto": "Punto 9C", "match": "contiene",
            "nombre": "transferí 20k a Santander desde Galicia: directo determinístico",
            "ejecutar": lambda: p9c_caso_1(datos),
            "esperado": (
                "Respuesta: Listo. Pasaste $20.000 de Galicia a Santander. | "
                "Saldos ajustados: True | Cero gastos: True | Llegó a la IA: False"
            ),
        },
        {
            "id": "P9C.2", "punto": "Punto 9C", "match": "exacto",
            "nombre": "pasé 20 lucas de galicia a macro: crea billetera con 1",
            "ejecutar": lambda: p9c_caso_2(datos),
            "esperado": (
                "No tenés Macro cargada.\n"
                "1. Crearla\n"
                "2. Era otra de tus cuentas\n"
                "3. Fue un pago a otra persona | "
                "Listo. Creé Macro y pasaste $20.000 de Galicia a Macro. | "
                "Macro: 20000.00 | Cero gastos: True | Llegó a la IA: False"
            ),
        },
        {
            "id": "P9C.3", "punto": "Punto 9C", "match": "exacto",
            "nombre": "pasé 20k de galicia a macro: opción 3 registra pago a persona",
            "ejecutar": lambda: p9c_caso_3(datos),
            "esperado": (
                "Listo. $20.000 en Pagos a personas desde Galicia — registrado.\n"
                "Si te acordás para qué era, decime «era el súper» y lo cambio. | "
                "Transferencias nuevas: 0 | Gastos nuevos: 1 | Llegó a la IA: False"
            ),
        },
        {
            "id": "P9C.4", "punto": "Punto 9C", "match": "exacto",
            "nombre": "múltiples transferencias en un mensaje y anulación con deshacer",
            "ejecutar": lambda: p9c_caso_4(datos),
            "esperado": (
                "Listo. Pasaste $20.000 de Galicia a Santander y $5.000 de Santander a "
                "Efectivo Pesos. | ¿Querés anular las 2 transferencias que registraste recién? "
                "¿Confirmás? | Listo, 2 transferencias anuladas. | Saldos intactos: True | "
                "Llegó a la IA: False"
            ),
        },
        {
            "id": "P9C.5", "punto": "Punto 9C", "match": "exacto",
            "nombre": "transferencia con fecha ayer registra con fecha resuelta",
            "ejecutar": lambda: p9c_caso_5(datos),
            "esperado": (
                "Listo. Pasaste $20.000 de Galicia a Santander (ayer). | "
                "Fecha ayer: True | Llegó a la IA: False"
            ),
        },
        {
            "id": "P9C.6", "punto": "Punto 9C", "match": "exacto",
            "nombre": "distinta moneda pregunta cotización sin llamar a IA",
            "ejecutar": lambda: p9c_caso_6(datos),
            "esperado": (
                "¿A qué cotización vendiste o cuántos pesos recibís en Galicia? | "
                "Llegó a la IA: False"
            ),
        },
    ]
