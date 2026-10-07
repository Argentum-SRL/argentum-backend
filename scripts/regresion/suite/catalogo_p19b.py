"""
scripts/regresion/suite/catalogo_p19b.py

Entradas de catálogo para los escenarios P19.10 a P19.21 de la suite de regresión de WhatsApp.
"""
from __future__ import annotations

from typing import Any

from scripts.regresion.suite.escenarios_p19b import (
    p19_caso_10,
    p19_caso_11,
    p19_caso_12,
    p19_caso_13,
    p19_caso_14,
    p19_caso_15,
    p19_caso_16,
    p19_caso_17,
    p19_caso_18,
    p19_caso_19,
    p19_caso_20,
    p19_caso_21,
)


def entradas_p19b(datos: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna las entradas de catálogo para los escenarios P19.10 a P19.21."""
    return [
        {
            "id": "P19.10", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con 2 gastos y 1 rendimiento, propuesta nombra rendimiento y registra 2 txs y 1 rendimiento con saldo",
            "ejecutar": lambda: p19_caso_10(datos),
            "esperado": "Propuesta rendimiento: True | Registrado tras sí: True | Txs creadas: 2 | Rends creados: 1 | Saldo subio: True",
        },
        {
            "id": "P19.11", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con rendimiento ya existente en esa fecha, aviso de ya tenías y no se duplica",
            "ejecutar": lambda: p19_caso_11(datos),
            "esperado": "Linea ya tenias: True | Txs creadas: 2 | Rends creados: 0",
        },
        {
            "id": "P19.12", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con solo rendimientos, mensaje fijo y nada confirmable pendiente",
            "ejecutar": lambda: p19_caso_12(datos),
            "esperado": "Mensaje solo rendimientos: True | Nada confirmable tras sí: True | Txs creadas: 0 | Rends creados: 0",
        },
        {
            "id": "P19.13", "punto": "Punto 19", "match": "exacto",
            "nombre": "Rendimiento con billetera que no rinde, aviso de no pude anotar y no se crea rendimiento",
            "ejecutar": lambda: p19_caso_13(datos),
            "esperado": "Linea no pude anotar: True | Txs creadas: 1 | Rends creados: 0",
        },
        {
            "id": "P19.14", "punto": "Punto 19", "match": "exacto",
            "nombre": "Respuesta no tras propuesta mixta cancela y no crea ni transacciones ni rendimientos",
            "ejecutar": lambda: p19_caso_14(datos),
            "esperado": "Cancelado tras no: True | Txs creadas: 0 | Rends creados: 0",
        },
        {
            "id": "P19.15", "punto": "Punto 19", "match": "exacto",
            "nombre": "Rendimiento menor o igual al ancla, aviso de ya tenías cargados hasta y no se anota",
            "ejecutar": lambda: p19_caso_15(datos),
            "esperado": "Aviso cobertura: True | Txs creadas: 1 | Rends creados: 0",
        },
        {
            "id": "P19.16", "punto": "Punto 19", "match": "exacto",
            "nombre": "Rendimiento de hace 70 días, aviso de no pude anotar y no se crea rendimiento",
            "ejecutar": lambda: p19_caso_16(datos),
            "esperado": "Aviso fecha vieja: True | Txs creadas: 1 | Rends creados: 0",
        },
        {
            "id": "P19.17", "punto": "Punto 19", "match": "exacto",
            "nombre": "Billetera con tna y es_inversion=false como única que rinde anota rendimiento tras sí",
            "ejecutar": lambda: p19_caso_17(datos),
            "esperado": "Propuesta tna: True | Registrado tras sí: True | Txs creadas: 1 | Rends creados: 1",
        },
        {
            "id": "P19.18", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con 2 Dinero disponible y 1 débito reparte billeteras y actualiza saldos",
            "ejecutar": lambda: p19_caso_18(datos),
            "esperado": "Propuesta billeteras: True | Txs creadas: 3 | Asignacion OK: True | Saldos OK: True",
        },
        {
            "id": "P19.19", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con común, pase propio, crédito y rechazado avisa salteos y anota 1 tx",
            "ejecutar": lambda: p19_caso_19(datos),
            "esperado": "Propuesta comun: True | Saltee exactos: True | Txs creadas: 1",
        },
        {
            "id": "P19.20", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura solo omitidos informa no encontré y líneas sin propuesta confirmable",
            "ejecutar": lambda: p19_caso_20(datos),
            "esperado": "Mensaje omitidos: True | Sin propuesta confirmable: True | Txs creadas: 0",
        },
        {
            "id": "P19.21", "punto": "Punto 19", "match": "exacto",
            "nombre": "Movimiento con igual monto y fecha en otra billetera no se marca duplicado",
            "ejecutar": lambda: p19_caso_21(datos),
            "esperado": "No es duplicado: True | Propone en MP: True",
        },
    ]
