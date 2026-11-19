"""
scripts/regresion/suite/catalogo_p16_p17.py

Entradas de catálogo para los escenarios P16.1 a P16.6, P10.13 y P17.1 a P17.15.
"""
from __future__ import annotations

from typing import Any

from scripts.regresion.suite.escenarios_p10 import p10_caso_13
from scripts.regresion.suite.escenarios_p14 import (
    p14_caso_1, p14_caso_2, p14_caso_3, p14_caso_4, p14_caso_5,
    p14_caso_6, p14_caso_7, p14_caso_8, p14_caso_9, p14_caso_10,
    p14_caso_11, p14_caso_12, p14_caso_13, p14_caso_14, p14_caso_15,
    p14_caso_16, p14_caso_17,
)
from scripts.regresion.suite.escenarios_p16 import (
    p16_caso_1,
    p16_caso_2,
    p16_caso_3,
    p16_caso_4,
    p16_caso_5,
    p16_caso_6,
)
from scripts.regresion.suite.escenarios_p17 import (
    p17_caso_1,
    p17_caso_2,
    p17_caso_3,
    p17_caso_4,
    p17_caso_5,
    p17_caso_6,
    p17_caso_7,
    p17_caso_8,
    p17_caso_9,
    p17_caso_10,
    p17_caso_11,
    p17_caso_12,
    p17_caso_13,
    p17_caso_14,
    p17_caso_15,
)


def entradas_p16_p17(datos: dict[str, Any]) -> list[dict[str, Any]]:
    """Retorna las entradas de catálogo para P14, P16, P10.13 y P17."""
    return [
        {
            "id": "P14.1", "punto": "Punto 14", "match": "exacto",
            "nombre": "gasté 5000 en el kiosco: registro directo en el acto",
            "ejecutar": lambda: p14_caso_1(datos),
            "esperado": (
                "Resp ok: True | Txs: 1 | Saldo: -5000.00 "
                "| Accion ejecutada: True | Propuesta pendiente: True"
            ),
        },
        {
            "id": "P14.2", "punto": "Punto 14", "match": "exacto",
            "nombre": "lote de 2: registra directo 2 movimientos con lote",
            "ejecutar": lambda: p14_caso_2(datos),
            "esperado": "Resp ok: True | Txs: 2 | Accion lote: True",
        },
        {
            "id": "P14.3", "punto": "Punto 14", "match": "exacto",
            "nombre": "ingreso simple: registra en el acto",
            "ejecutar": lambda: p14_caso_3(datos),
            "esperado": "Resp ok: True | Txs: 1 | Saldo: +800000.00",
        },
        {
            "id": "P14.4", "punto": "Punto 14", "match": "exacto",
            "nombre": "compra con tarjeta en cuotas: pide confirmación",
            "ejecutar": lambda: p14_caso_4(datos),
            "esperado": (
                "Pide conf: True | Txs antes de si: 0 "
                "| Txs despues de si: True | Confirma ok: True"
            ),
        },
        {
            "id": "P14.5", "punto": "Punto 14", "match": "exacto",
            "nombre": "el mismo gasto dos veces seguidas: pregunta por duplicado",
            "ejecutar": lambda: p14_caso_5(datos),
            "esperado": "Pregunta duplicado: True | Txs totales: 1",
        },
        {
            "id": "P14.6", "punto": "Punto 14", "match": "exacto",
            "nombre": "confianza 0.65 (debajo del umbral): propuesta y 0 txs",
            "ejecutar": lambda: p14_caso_6(datos),
            "esperado": "Coincide con HEAD: True | Txs creadas: 0",
        },
        {
            "id": "P14.7", "punto": "Punto 14", "match": "exacto",
            "nombre": "sí justo después de un registro directo: mensaje claro",
            "ejecutar": lambda: p14_caso_7(datos),
            "esperado": "Resp ok: True | Txs nuevas: 0",
        },
        {
            "id": "P14.8", "punto": "Punto 14", "match": "exacto",
            "nombre": "registro directo, deshacer y sí: borra y restaura saldo",
            "ejecutar": lambda: p14_caso_8(datos),
            "esperado": (
                "Deshacer ok: True | Txs restantes: 0 | Saldo restaurado: True"
            ),
        },
        {
            "id": "P14.9", "punto": "Punto 14", "match": "exacto",
            "nombre": "registro directo y eran 3000 no 5000: corrige monto",
            "ejecutar": lambda: p14_caso_9(datos),
            "esperado": "Monto corregido: True | Saldo ok: True | Confirmacion: True",
        },
        {
            "id": "P14.10", "punto": "Punto 14", "match": "exacto",
            "nombre": "falla forzada: rollback limpio y 0 txs",
            "ejecutar": lambda: p14_caso_10(datos),
            "esperado": (
                "Sin listo: True | Txs creadas: 0 | Saldo intacto: True "
                "| Propuesta pendiente: True | Txs despues si: 0"
            ),
        },
        {
            "id": "P14.11", "punto": "Punto 14", "match": "exacto",
            "nombre": "idempotencia: mismo wamid dos veces resulta en 1 tx",
            "ejecutar": lambda: p14_caso_11(datos),
            "esperado": "Txs creadas: 1",
        },
        {
            "id": "P14.12", "punto": "Punto 14", "match": "exacto",
            "nombre": "billetera ambigua: pregunta cuál y al responder registra",
            "ejecutar": lambda: p14_caso_12(datos),
            "esperado": (
                "Pregunta ok: True | Txs antes eleccion: 0 "
                "| Registro ok: True | Txs creadas: 1"
            ),
        },
        {
            "id": "P14.13", "punto": "Punto 14", "match": "exacto",
            "nombre": "origen imagen con billetera pendiente: pide confirmación",
            "ejecutar": lambda: p14_caso_13(datos),
            "esperado": (
                "Pide conf: True | Txs antes si: 0 "
                "| Txs despues si: 1 | Confirma ok: True"
            ),
        },
        {
            "id": "P14.14", "punto": "Punto 14", "match": "exacto",
            "nombre": "lote con todos los ítems inválidos: mensaje claro, 0 txs",
            "ejecutar": lambda: p14_caso_14(datos),
            "esperado": "Msg ok: True | Txs creadas: 0 | Propuesta pendiente: True",
        },
        {
            "id": "P14.15", "punto": "Punto 14", "match": "exacto",
            "nombre": "lote mixto inválido y válido: registra con aviso previo",
            "ejecutar": lambda: p14_caso_15(datos),
            "esperado": "Aviso descarte: True | Listo ok: True | Orden ok: True | Txs: 1",
        },
        {
            "id": "P14.16", "punto": "Punto 14", "match": "exacto",
            "nombre": "confianza baja con billetera pendiente: pide confirmación",
            "ejecutar": lambda: p14_caso_16(datos),
            "esperado": (
                "Pide conf: True | Txs antes si: 0 "
                "| Txs despues si: 1 | Confirma ok: True"
            ),
        },
        {
            "id": "P14.17", "punto": "Punto 14", "match": "exacto",
            "nombre": "control sin marcas con billetera pendiente: registra directo",
            "ejecutar": lambda: p14_caso_17(datos),
            "esperado": "Registra directo: True | Txs: 1",
        },
        {
            "id": "P16.1", "punto": "Punto 16", "match": "exacto",
            "nombre": "Aporte simple con meta y monto claros: confirma y crea tx",
            "ejecutar": lambda: p16_caso_1(datos),
            "esperado": (
                "Propuesta ok: True | Confirmado ok: True | Monto sumado: 15000 "
                "| Tx desc ok: True | Saldo Galicia ok: True"
            ),
        },
        {
            "id": "P16.2", "punto": "Punto 16", "match": "exacto",
            "nombre": "Meta ambigua: pregunta cuál antes de proponer",
            "ejecutar": lambda: p16_caso_2(datos),
            "esperado": (
                "Pregunta ambigua: True | Txs creadas: 0 "
                "| Sin propuesta pendiente: True"
            ),
        },
        {
            "id": "P16.3", "punto": "Punto 16", "match": "exacto",
            "nombre": "Meta inexistente: mensaje claro, no propone nada",
            "ejecutar": lambda: p16_caso_3(datos),
            "esperado": (
                "Mensaje claro: True | Txs creadas: 0 | Sin propuesta pendiente: True"
            ),
        },
        {
            "id": "P16.4", "punto": "Punto 16", "match": "exacto",
            "nombre": "Monto que completa o supera el objetivo: confirma y felicita",
            "ejecutar": lambda: p16_caso_4(datos),
            "esperado": (
                "Propuesta ok: True | Felicita: True | Monto actual: 13000 "
                "| Estado completada: True"
            ),
        },
        {
            "id": "P16.5", "punto": "Punto 16", "match": "exacto",
            "nombre": "'No' cancela la propuesta sin tocar la meta",
            "ejecutar": lambda: p16_caso_5(datos),
            "esperado": (
                "Cancelado: True | Meta intacta: True | Txs creadas: 0 "
                "| Saldo intacto: True"
            ),
        },
        {
            "id": "P16.6", "punto": "Punto 16", "match": "exacto",
            "nombre": "Deshacer aporte recién confirmado: revierte meta y borra tx",
            "ejecutar": lambda: p16_caso_6(datos),
            "esperado": (
                "Propuesta deshacer ok: True | Confirmacion deshacer ok: True "
                "| Meta revertida: True | Tx borrada: True | Saldo restaurado: True"
            ),
        },
        {
            "id": "P10.13", "punto": "Punto 10", "match": "exacto",
            "nombre": "alta de gimnasio por WhatsApp con categoría sugerida",
            "ejecutar": lambda: p10_caso_13(datos),
            "esperado": "Cat: Ocio / Deportes y gimnasio | Conf: True",
        },
        {
            "id": "P17.1", "punto": "Punto 17", "match": "exacto",
            "nombre": "mensaje con fecha y Uber es nuevo movimiento, no corrección",
            "ejecutar": lambda: p17_caso_1(datos),
            "esperado": (
                "No correccion: True | Nuevo monto 5456: True "
                "| Subcat Taxi/Apps: True | Fecha 23/09: True | Anterior intacto: True"
            ),
        },
        {
            "id": "P17.2", "punto": "Punto 17", "match": "exacto",
            "nombre": "lote de 3 movimientos con fecha previa y transferencia recibida",
            "ejecutar": lambda: p17_caso_2(datos),
            "esperado": (
                "Lote 3 txs: True | 2 egresos 1 ingreso: True | Todas 27/09: True"
            ),
        },
        {
            "id": "P17.3", "punto": "Punto 17", "match": "exacto",
            "nombre": "lote con frase introductoria y fecha previa",
            "ejecutar": lambda: p17_caso_3(datos),
            "esperado": "Lote 2 txs: True | 2 egresos: True | Ambas 27/09: True",
        },
        {
            "id": "P17.4", "punto": "Punto 17", "match": "exacto",
            "nombre": "corrección explícita de fecha y visualización en Ahora",
            "ejecutar": lambda: p17_caso_4(datos),
            "esperado": (
                "Propuesta visible fecha: True | Confirmado ok: True "
                "| Fecha actualizada: True"
            ),
        },
        {
            "id": "P17.5", "punto": "Punto 17", "match": "exacto",
            "nombre": "lote mixto con Uber y transferencia a persona no se bloquea",
            "ejecutar": lambda: p17_caso_5(datos),
            "esperado": "Sin bloqueo: True | 2 egresos creados: True",
        },
        {
            "id": "P17.6", "punto": "Punto 17", "match": "exacto",
            "nombre": "lote con transferencia entre cuentas propias se bloquea",
            "ejecutar": lambda: p17_caso_6(datos),
            "esperado": "Bloqueo transferencias propias: True | Creadas: 0",
        },
        {
            "id": "P17.7", "punto": "Punto 17", "match": "exacto",
            "nombre": "Didi clasificado en Transporte / Taxi / Apps",
            "ejecutar": lambda: p17_caso_7(datos),
            "esperado": "Subcategoria Taxi/Apps: True | Categoria Transporte: True",
        },
        {
            "id": "P17.8", "punto": "Punto 17", "match": "exacto",
            "nombre": "Didi Taxi vs Rappi vs Didi Food",
            "ejecutar": lambda: p17_caso_8(datos),
            "esperado": "Categorias ajustadas ok: True",
        },
        {
            "id": "P17.9", "punto": "Punto 17", "match": "exacto",
            "nombre": "ingreso con fecha distinta no dispara pregunta de duplicado",
            "ejecutar": lambda: p17_caso_9(datos),
            "esperado": (
                "Sin pregunta duplicado: True | Segundo ingreso: True "
                "| Fecha distinta 22/09: True | Fecha igual IA: True"
            ),
        },
        {
            "id": "P17.10", "punto": "Punto 17", "match": "exacto",
            "nombre": "confirmación de lote tras duplicado usa signos y categorías",
            "ejecutar": lambda: p17_caso_10(datos),
            "esperado": "Pregunta duplicado ok: True | Confirmacion con signos: True",
        },
        {
            "id": "P17.11", "punto": "Punto 17", "match": "exacto",
            "nombre": "corrección real de monto sigue funcionando",
            "ejecutar": lambda: p17_caso_11(datos),
            "esperado": "Propuesta correccion monto: True | Monto corregido: True",
        },
        {
            "id": "P17.12", "punto": "Punto 17", "match": "exacto",
            "nombre": "le transferí a persona es egreso",
            "ejecutar": lambda: p17_caso_12(datos),
            "esperado": "Tipo egreso: True",
        },
        {
            "id": "P17.13", "punto": "Punto 17", "match": "exacto",
            "nombre": "persona me transfirió es ingreso",
            "ejecutar": lambda: p17_caso_13(datos),
            "esperado": "Tipo ingreso: True",
        },
        {
            "id": "P17.14", "punto": "Punto 17", "match": "exacto",
            "nombre": "pago a tercero y transferencia entre cuentas propias se bloquea",
            "ejecutar": lambda: p17_caso_14(datos),
            "esperado": "Bloqueo transferencias propias: True | Creadas: 0",
        },
        {
            "id": "P17.15", "punto": "Punto 17", "match": "exacto",
            "nombre": "lote con fechas intermedias propaga fecha previa",
            "ejecutar": lambda: p17_caso_15(datos),
            "esperado": "Lote 3 txs: True | Fechas 25/09 25/09 27/09: True",
        },
    ]
