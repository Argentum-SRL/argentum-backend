"""Catálogo ordenado de los 174 escenarios de la suite de regresión de WhatsApp."""
from __future__ import annotations

from datetime import timedelta

from scripts.regresion.suite.escenarios_p03 import (
    p3_caso_1, p3_caso_2, p3_caso_3, p3_caso_4, p3_caso_5,
    p3_caso_6, p3_caso_7, p3_caso_8, p3_caso_9, p3_caso_10,
)
from scripts.regresion.suite.escenarios_p04 import (
    p4_caso_1, p4_caso_2, p4_caso_3, p4_caso_4, p4_caso_5,
    p4_caso_6, p4_caso_7, p4_caso_8, p4_variante_no,
)
from scripts.regresion.suite.escenarios_p05 import (
    p5_caso_1, p5_caso_2, p5_caso_3, p5_caso_4, p5_caso_5,
    p5_caso_6_concurrente, p5_caso_7, p5_caso_cuotas,
)
from scripts.regresion.suite.escenarios_p06_p07 import (
    p6_ejecutar_caso, p7_ejecutar_caso,
)
from scripts.regresion.suite.escenarios_p08 import (
    p8_caso_1, p8_caso_2, p8_caso_3, p8_caso_4, p8_caso_5,
    p8_caso_5b, p8_caso_5c, p8_caso_5d, p8_caso_6, p8_caso_7,
    p8_caso_8, p8_caso_9, p8_caso_10, p8_caso_11,
)
from scripts.regresion.suite.escenarios_p09 import (
    p9_caso_1, p9_caso_2, p9_caso_3, p9_caso_4, p9_caso_5, p9_caso_6,
    p9_caso_7, p9_caso_8, p9_caso_9, p9_caso_10, p9_caso_11, p9_caso_12,
    p9b_caso_1, p9b_caso_2, p9b_caso_3, p9b_caso_4, p9b_caso_5,
    p9b_caso_6, p9b_caso_7, p9b_caso_8, p9b_caso_9, p9b_caso_10,
    p9b_caso_11, p9b_caso_12, p9b_caso_13, p9b_caso_14, p9b_caso_15,
    p9b_caso_16, p9b_caso_17, p9b_caso_18, p9b_caso_19, p9b_caso_20,
)
from scripts.regresion.suite.escenarios_p10 import (
    p10_caso_1, p10_caso_2, p10_caso_3, p10_caso_4, p10_caso_5,
    p10_caso_6, p10_caso_7, p10_caso_8, p10_caso_9, p10_caso_10,
    p10_caso_11, p10_caso_12,
)
from scripts.regresion.suite.escenarios_p11 import (
    p11_caso_1, p11_caso_2, p11_caso_3, p11_caso_4, p11_caso_5,
    p11_caso_6, p11_caso_7, p11_caso_8, p11_caso_9, p11_caso_10,
    p11_caso_11, p11_caso_12, p11_caso_13,
)
from scripts.regresion.suite.escenarios_p12 import (
    p12_caso_1, p12_caso_2, p12_caso_3, p12_caso_4, p12_caso_5,
    p12_caso_6, p12_caso_7, p12_caso_8, p12_caso_9, p12_caso_10,
    p12_caso_11, p12_caso_12, p12_caso_13,
)
from scripts.regresion.suite.escenarios_p13 import (
    p13_caso_1, p13_caso_2, p13_caso_3, p13_caso_4, p13_caso_5,
)
from scripts.regresion.suite.escenarios_p18 import (
    p18_caso_1,
    p18_caso_2,
    p18_caso_3,
    p18_caso_4,
)
from scripts.regresion.suite.escenarios_p19 import (
    p19_caso_1,
    p19_caso_2,
    p19_caso_3,
    p19_caso_4,
    p19_caso_5,
    p19_caso_6,
    p19_caso_7,
    p19_caso_8,
    p19_caso_9,
)
from scripts.regresion.suite.catalogo_p09c import entradas_p09c
from scripts.regresion.suite.catalogo_p16_p17 import entradas_p16_p17
from scripts.regresion.suite.catalogo_p18b import entradas_p18b
from scripts.regresion.suite.catalogo_p19b import entradas_p19b
from scripts.regresion.suite.escenarios_p20 import entradas_p20
from scripts.regresion.suite.escenarios_p21 import entradas_p21
from scripts.regresion.suite.escenarios_p22 import entradas_p22
from scripts.regresion.suite.escenarios_p23 import entradas_p23


def obtener_catalogo(datos: dict, hoy=None, ayer=None) -> list[dict]:
    if hoy is None:
        from app.utils.fecha import hoy_argentina
        hoy = hoy_argentina()
    if ayer is None:
        ayer = hoy - timedelta(days=1)

    from app.core.reglas_texto_wpp import fecha_corta_wpp
    fecha_p63 = hoy - timedelta(days=20)
    frase_p63 = fecha_corta_wpp(fecha_p63, hoy)

    catalogo = [
        {
            "id": "P3.1", "punto": "Punto 3", "match": "exacto",
            "nombre": "Usuario con billetera principal dice 'gasté 5000 en el kiosco' sin nombrar billetera",
            "ejecutar": lambda: p3_caso_1(datos),
            "esperado": "Listo, anoté $5.000 en Kiosco desde Galicia.",
        },
        {
            "id": "P3.2", "punto": "Punto 3", "match": "exacto",
            "nombre": "Usuario sin billetera principal, lo mismo",
            "ejecutar": lambda: p3_caso_2(datos),
            "esperado": (
                "¿De qué cuenta salió?\n1. Efectivo Pesos\n2. Galicia\n3. Santander"
            ),
        },
        {
            "id": "P3.3", "punto": "Punto 3", "match": "exacto",
            "nombre": "Responde '2' a un menú de billeteras",
            "ejecutar": lambda: p3_caso_3(datos),
            "esperado": "Listo, anoté $5.000 en Kiosco desde Galicia.",
        },
        {
            "id": "P3.4", "punto": "Punto 3", "match": "exacto",
            "nombre": "Responde con el nombre de la billetera en vez del número",
            "ejecutar": lambda: p3_caso_4(datos),
            "esperado": "Listo, anoté $5.000 en Kiosco desde Santander.",
        },
        {
            "id": "P3.5", "punto": "Punto 3", "match": "exacto",
            "nombre": "Responde un número fuera de rango",
            "ejecutar": lambda: p3_caso_5(datos),
            "esperado": "Ese número no está en la lista. Elegí del 1 al 3.",
        },
        {
            "id": "P3.6", "punto": "Punto 3", "match": "exacto",
            "nombre": "Manda un número sin ninguna pregunta pendiente",
            "ejecutar": lambda: p3_caso_6(datos),
            "esperado": (
                "Mandaste solo un número. Decime en qué, por ejemplo: _5000 kiosco_."
            ),
        },
        {
            "id": "P3.7", "punto": "Punto 3", "match": "exacto",
            "nombre": "Responde a un menú 31 minutos después",
            "ejecutar": lambda: p3_caso_7(datos),
            "esperado": "Pasó mucho tiempo y lo descarté. Mandámelo de nuevo.",
        },
        {
            "id": "P3.8", "punto": "Punto 3", "match": "exacto",
            "nombre": "Recibe una propuesta y responde 'no, fue en Santander'",
            "ejecutar": lambda: p3_caso_8(datos),
            "esperado": "¿Anoto $5.000 en Kiosco desde Santander?",
        },
        {
            "id": "P3.9", "punto": "Punto 3", "match": "exacto",
            "nombre": "Dice 'cobré 800000 de sueldo' y elige billetera de destino",
            "ejecutar": lambda: p3_caso_9(datos),
            "esperado": (
                "¿A qué cuenta entró?\n1. Efectivo Pesos\n2. Galicia\n3. Santander\n---\n"
                "Listo, anoté un ingreso de $800.000 en Efectivo Pesos (Sueldo)."
            ),
        },
        {
            "id": "P3.10", "punto": "Punto 3", "match": "exacto",
            "nombre": "Usuario con una sola billetera en pesos dice 'gasté 5000'",
            "ejecutar": lambda: p3_caso_10(datos),
            "esperado": (
                "Listo, anoté $5.000 en Otros desde Efectivo Pesos.\n"
                "Efectivo Pesos quedó en negativo."
            ),
        },
        {
            "id": "P4.1", "punto": "Punto 4", "match": "exacto",
            "nombre": "Operación a medias y manda 'hola'",
            "ejecutar": lambda: p4_caso_1(datos),
            "esperado": (
                "¡Hola! Quedó a medias anotar $5.000 en Kiosco.\n"
                "Contestá la pregunta de arriba o mandame otro gasto."
            ),
        },
        {
            "id": "P4.2", "punto": "Punto 4", "match": "exacto",
            "nombre": "Operación a medias y manda un gasto distinto",
            "ejecutar": lambda: p4_caso_2(datos),
            "esperado": (
                "Dejé sin anotar los $5.000 de Kiosco.\n"
                "Vamos con los $12.000 de Verdulería:\n\n"
                "¿De qué cuenta salió?\n1. Efectivo Pesos\n2. Galicia\n3. Santander"
            ),
        },
        {
            "id": "P4.3", "punto": "Punto 4", "match": "exacto",
            "nombre": "Recibe una propuesta y responde 'no'",
            "ejecutar": lambda: p4_caso_3(datos),
            "esperado": "Listo, cancelado.",
        },
        {
            "id": "P4.4", "punto": "Punto 4", "match": "exacto",
            "nombre": "Tras cancelar manda 'dale' (verifica 0 txs en BD)",
            "ejecutar": lambda: p4_caso_4(datos),
            "esperado": "No tengo nada pendiente para confirmar. (txs_creadas=0)",
        },
        {
            "id": "P4.5", "punto": "Punto 4", "match": "exacto",
            "nombre": "Manda 'buenas' sin nada pendiente",
            "ejecutar": lambda: p4_caso_5(datos),
            "esperado": (
                "¡Hola! Mandame un gasto, como _gasté 5000 en el kiosco_, "
                "o preguntá _cuánta plata tengo_."
            ),
        },
        {
            "id": "P4.6", "punto": "Punto 4", "match": "exacto",
            "nombre": "Manda 'cuánto gasté en pizza'",
            "ejecutar": lambda: p4_caso_6(datos),
            "esperado": "Intent: consultar_gastos | Respuesta ok: True",
        },
        {
            "id": "P4.7", "punto": "Punto 4", "match": "exacto",
            "nombre": "Responde 31 minutos después",
            "ejecutar": lambda: p4_caso_7(datos),
            "esperado": "Pasó mucho tiempo y lo descarté. Mandámelo de nuevo.",
        },
        {
            "id": "P4.8", "punto": "Punto 4", "match": "exacto",
            "nombre": "Manda 'sí' sin nada pendiente",
            "ejecutar": lambda: p4_caso_8(datos),
            "esperado": "No tengo nada pendiente para confirmar.",
        },
        {
            "id": "P4.VAR1", "punto": "Punto 4", "match": "exacto",
            "nombre": "Variante de no: 'no'",
            "ejecutar": lambda: p4_variante_no(datos, "no"),
            "esperado": "Listo, cancelado.",
        },
        {
            "id": "P4.VAR2", "punto": "Punto 4", "match": "exacto",
            "nombre": "Variante de no: 'no, fue en Santander'",
            "ejecutar": lambda: p4_variante_no(datos, "no, fue en Santander", billetera_propuesta="Galicia"),
            "esperado": "¿Anoto $5.000 en Kiosco desde Santander?",
        },
        {
            "id": "P4.VAR3", "punto": "Punto 4", "match": "exacto",
            "nombre": "Variante de no: 'no fue en galicia'",
            "ejecutar": lambda: p4_variante_no(datos, "no fue en galicia", billetera_propuesta="Santander"),
            "esperado": "¿Anoto $5.000 en Kiosco desde Galicia?",
        },
        {
            "id": "P4.VAR4", "punto": "Punto 4", "match": "exacto",
            "nombre": "Variante de no: 'no, cancelá'",
            "ejecutar": lambda: p4_variante_no(datos, "no, cancelá"),
            "esperado": "Listo, cancelado.",
        },
        {
            "id": "P4.VAR5", "punto": "Punto 4", "match": "exacto",
            "nombre": "Variante de no: 'nooo'",
            "ejecutar": lambda: p4_variante_no(datos, "nooo"),
            "esperado": "Listo, cancelado.",
        },
        {
            "id": "P4.VAR6", "punto": "Punto 4", "match": "exacto",
            "nombre": "Variante de no: 'no gracias'",
            "ejecutar": lambda: p4_variante_no(datos, "no gracias"),
            "esperado": "Listo, cancelado.",
        },
        {
            "id": "P5.1", "punto": "Punto 5", "match": "contiene",
            "nombre": "Gasto repetido a los cinco minutos",
            "ejecutar": lambda: p5_caso_1(datos),
            "esperado": "ya anotaste $5.000 en Kiosco. ¿Lo anoto otra vez?",
        },
        {
            "id": "P5.2", "punto": "Punto 5", "match": "contiene",
            "nombre": "Ante la pregunta de duplicado, responde que es nuevo",
            "ejecutar": lambda: p5_caso_2(datos),
            "esperado": "Listo, anoté $5.000 en Kiosco desde Galicia.",
        },
        {
            "id": "P5.3", "punto": "Punto 5", "match": "exacto",
            "nombre": "Ante la pregunta de duplicado, responde que es un error",
            "ejecutar": lambda: p5_caso_3(datos),
            "esperado": "Listo, no lo anoto.",
        },
        {
            "id": "P5.4", "punto": "Punto 5", "match": "exacto",
            "nombre": "Gasto igual de hace dos horas (>1h)",
            "ejecutar": lambda: p5_caso_4(datos),
            "esperado": "Listo, anoté $5.000 en Kiosco desde Galicia.",
        },
        {
            "id": "P5.5", "punto": "Punto 5", "match": "exacto",
            "nombre": "Mismo monto, otra categoría (sin advertencia)",
            "ejecutar": lambda: p5_caso_5(datos),
            "esperado": "Listo, anoté $5.000 en Farmacia desde Galicia.",
        },
        {
            "id": "P5.6", "punto": "Punto 5", "match": "exacto",
            "nombre": "Dos confirmaciones concurrentes",
            "ejecutar": lambda: p5_caso_6_concurrente(datos),
            "esperado": "Exitos=1, Rechazados_por_concurrencia=1",
        },
        {
            "id": "P5.7", "punto": "Punto 5", "match": "exacto",
            "nombre": "Lote con dos movimientos idénticos",
            "ejecutar": lambda: p5_caso_7(datos),
            "esperado": "Mandaste 2 veces $5.000 en Kiosco. ¿Anoto los dos?",
        },
        {
            "id": "P5.CUOTAS", "punto": "Punto 5", "match": "exacto",
            "nombre": "Cuotas de tarjeta no disparan falso positivo de duplicado",
            "ejecutar": lambda: p5_caso_cuotas(datos),
            "esperado": "Listo, anoté $5.000 en Kiosco desde Galicia.",
        },
        {
            "id": "P6.1", "punto": "Punto 6", "match": "exacto",
            "nombre": "Gasto de hoy",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto hoy", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": (
                "Propuesta:\n"
                "¿Anoto $5.000 en Kiosco desde Galicia?\n"
                "Confirmación:\n"
                "Listo, anoté $5.000 en Kiosco desde Galicia."
            ),
        },
        {
            "id": "P6.2", "punto": "Punto 6", "match": "exacto",
            "nombre": "Gasto de ayer",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto ayer", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": ayer.isoformat()
            }),
            "esperado": (
                "Propuesta:\n"
                "¿Anoto $5.000 en Kiosco desde Galicia (ayer)?\n"
                "Confirmación:\n"
                "Listo, anoté $5.000 en Kiosco desde Galicia (ayer)."
            ),
        },
        {
            "id": "P6.3", "punto": "Punto 6", "match": "exacto",
            "nombre": "Gasto del 31 de agosto",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 31 agosto", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": fecha_p63.isoformat()
            }),
            "esperado": (
                "Propuesta:\n"
                f"¿Anoto $5.000 en Kiosco desde Galicia ({frase_p63})?\n"
                "Confirmación:\n"
                f"Listo, anoté $5.000 en Kiosco desde Galicia ({frase_p63})."
            ),
        },
        {
            "id": "P6.4", "punto": "Punto 6", "match": "exacto",
            "nombre": "Gasto de hace tres meses (>60 días)",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 3 meses", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2026-06-03"
            }),
            "esperado": (
                "Propuesta:\n"
                "Solo anoto hasta 60 días para atrás: va con fecha de hoy.\n"
                "¿Anoto $5.000 en Kiosco desde Galicia?\n"
                "Confirmación:\n"
                "Listo, anoté $5.000 en Kiosco desde Galicia."
            ),
        },
        {
            "id": "P6.5", "punto": "Punto 6", "match": "exacto",
            "nombre": "Gasto con fecha futura",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto futuro", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2030-03-15"
            }),
            "esperado": (
                "Propuesta:\n"
                "Todavía no pasó esa fecha: va con fecha de hoy.\n"
                "¿Anoto $5.000 en Kiosco desde Galicia?\n"
                "Confirmación:\n"
                "Listo, anoté $5.000 en Kiosco desde Galicia."
            ),
        },
        {
            "id": "P6.6", "punto": "Punto 6", "match": "exacto",
            "nombre": "Gasto en dólares",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto USD", {
                "monto": 50, "moneda": "USD", "tipo": "egreso", "categoria": "Otros", "billetera_origen": "Efectivo Dólares", "fecha": hoy.isoformat()
            }, forzar_cero=True),
            "esperado": (
                "Propuesta:\n"
                "¿Anoto US$50 en Otros desde Efectivo Dólares?\n"
                "Confirmación:\n"
                "Listo, anoté US$50 en Otros desde Efectivo Dólares.\n"
                "Efectivo Dólares quedó en negativo."
            ),
        },
        {
            "id": "P6.7", "punto": "Punto 6", "match": "exacto",
            "nombre": "Lote con uno descartado",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Lote descalce", {
                "monto": 1000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Efectivo Pesos", "fecha": hoy.isoformat(),
                "transacciones_adicionales": [
                    {"monto": 2000, "moneda": "ARS", "tipo": "egreso", "categoria": "Panadería", "fecha": ayer.isoformat()},
                    {"monto": 10, "moneda": "USD", "tipo": "egreso", "categoria": "Farmacia", "fecha": hoy.isoformat()}
                ]
            }, forzar_cero=True),
            "esperado": (
                "Propuesta:\n"
                "No pude anotar Farmacia (US$10): "
                "es en dólares y Efectivo Pesos es en pesos.\n"
                "¿Anoto estos 2 gastos desde Efectivo Pesos?\n"
                "- $1.000 en Kiosco\n"
                "- $2.000 en Panadería · ayer\n"
                "Confirmación:\n"
                "Listo, anoté 2 gastos desde Efectivo Pesos:\n"
                "- $1.000 en Kiosco\n"
                "- $2.000 en Panadería · ayer\n"
                "Efectivo Pesos quedó en negativo."
            ),
        },
        {
            "id": "P6.8", "punto": "Punto 6", "match": "exacto",
            "nombre": "Gasto que deja la billetera en negativo",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto negativo", {
                "monto": 5000000, "moneda": "ARS", "tipo": "egreso", "categoria": "Supermercado", "billetera_origen": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": (
                "Propuesta:\n"
                "¿Anoto $5.000.000 en Supermercado desde Galicia?\n"
                "Confirmación:\n"
                "Listo, anoté $5.000.000 en Supermercado desde Galicia.\n"
                "Galicia quedó en negativo."
            ),
        },
        {
            "id": "P6.9", "punto": "Punto 6", "match": "exacto",
            "nombre": "Ingreso",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Ingreso", {
                "monto": 80000, "moneda": "ARS", "tipo": "ingreso", "categoria": "Sueldo", "billetera_destino": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": (
                "Propuesta:\n"
                "¿Anoto un ingreso de $80.000 en Galicia (Sueldo)?\n"
                "Confirmación:\n"
                "Listo, anoté un ingreso de $80.000 en Galicia (Sueldo)."
            ),
        },
        {
            "id": "P6.10", "punto": "Punto 6", "match": "exacto",
            "nombre": "Lote con todos descartados",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Lote todos descartados", {
                "monto": 10, "moneda": "USD", "tipo": "egreso", "categoria": "Farmacia", "billetera_origen": "Efectivo Pesos", "fecha": hoy.isoformat(),
                "transacciones_adicionales": [
                    {"monto": 20, "moneda": "USD", "tipo": "egreso", "categoria": "Supermercado", "fecha": hoy.isoformat()}
                ]
            }),
            "esperado": (
                "Propuesta:\n"
                "No pude anotar Farmacia (US$10): "
                "es en dólares y Efectivo Pesos es en pesos.\n"
                "No pude anotar Supermercado (US$20): "
                "es en dólares y Efectivo Pesos es en pesos.\n"
                "No pude anotar ninguno.\n"
                "Confirmación:\n"
                "NO_APLICA"
            ),
        },
        {
            "id": "P7.1", "punto": "Punto 7", "match": "exacto",
            "nombre": "Golosinas -> Kiosco (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 5000 en golosinas", "Kiosco", "Golosinas"),
            "esperado": "Cat: Kiosco | Desc: Golosinas",
        },
        {
            "id": "P7.2", "punto": "Punto 7", "match": "exacto",
            "nombre": "Verdulería -> Verdulería (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 8000 en la verdulería", "Verdulería", "Verdulería"),
            "esperado": "Cat: Verdulería | Desc: Verdulería",
        },
        {
            "id": "P7.3", "punto": "Punto 7", "match": "exacto",
            "nombre": "Nafta -> Combustible (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "cargué 30000 de nafta", "Combustible", "Nafta"),
            "esperado": "Cat: Combustible | Desc: Nafta",
        },
        {
            "id": "P7.4", "punto": "Punto 7", "match": "exacto",
            "nombre": "Prepaga -> Obra social / Prepaga (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "pagué 12000 de la prepaga", "Obra social / Prepaga", "Prepaga"),
            "esperado": "Cat: Obra social / Prepaga | Desc: Prepaga",
        },
        {
            "id": "P7.5", "punto": "Punto 7", "match": "exacto",
            "nombre": "Bondi -> Transporte público (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 4000 en el bondi", "Transporte público", "Bondi"),
            "esperado": "Cat: Transporte público | Desc: Bondi",
        },
        {
            "id": "P7.6", "punto": "Punto 7", "match": "exacto",
            "nombre": "Corte de pelo -> Cuidado personal (jerga argentina + descripción preservada)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "me corté el pelo, 15000", "Cuidado personal", "Corte de pelo"),
            "esperado": "Cat: Cuidado personal | Desc: Corte de pelo",
        },
        {
            "id": "P7.7", "punto": "Punto 7", "match": "exacto",
            "nombre": "Concepto raro -> Otros (prohibición de categorías inventadas + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 2500 en un coso cuántico intergaláctico", "Otros", "Coso cuántico intergaláctico"),
            "esperado": "Cat: Otros | Desc: Coso cuántico intergaláctico",
        },
        {
            "id": "P8.1", "punto": "Punto 8", "match": "exacto",
            "nombre": "Registrar un gasto y decir 'borrá eso', confirmar, verificar borrado y reversión de saldo",
            "ejecutar": lambda: p8_caso_1(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Borro $5.000 en Kiosco desde Galicia?\n"
                "Confirmación:\n"
                "Listo, lo borré.\n"
                "Borrado: True | Saldo restaurado: True"
            ),
        },
        {
            "id": "P8.2", "punto": "Punto 8", "match": "exacto",
            "nombre": "Decir 'borrá eso' sin nada registrado",
            "ejecutar": lambda: p8_caso_2(datos),
            "esperado": (
                "No anotaste nada por acá en los últimos 30 minutos. "
                "Borralo en miargentum.com"
            ),
        },
        {
            "id": "P8.3", "punto": "Punto 8", "match": "exacto",
            "nombre": "Decir 'borrá eso' dos veces seguidas",
            "ejecutar": lambda: p8_caso_3(datos),
            "esperado": "No hay nada para borrar.",
        },
        {
            "id": "P8.4", "punto": "Punto 8", "match": "exacto",
            "nombre": "Registrar gasto y decir 'eran 3.000 no 30.000', confirmar, verificar monto y saldo",
            "ejecutar": lambda: p8_caso_4(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Lo cambio de $30.000 a $3.000?\n"
                "Confirmación:\n"
                "Listo, quedó $3.000 en Supermercado desde Galicia.\n"
                "Monto corregido: True | Saldo ajustado (+27k): True"
            ),
        },
        {
            "id": "P8.5", "punto": "Punto 8", "match": "exacto",
            "nombre": "Registrar un gasto y decir 'eso era supermercado', verificar la categoría",
            "ejecutar": lambda: p8_caso_5(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Lo cambio de Kiosco a Supermercado?\n"
                "Confirmación:\n"
                "Listo, lo pasé a Supermercado.\n"
                "¿Siempre que digas _kiosco_ lo pongo ahí?\n"
                "Categoría final: Supermercado"
            ),
        },
        {
            "id": "P8.5b", "punto": "Punto 8", "match": "exacto",
            "nombre": "Confirmar memoria de comercio ('sí') tras corregir categoría",
            "ejecutar": lambda: p8_caso_5b(datos),
            "esperado": "Respuesta memoria:\nListo, lo voy a recordar.\nMemoria guardada: True",
        },
        {
            "id": "P8.5c", "punto": "Punto 8", "match": "exacto",
            "nombre": "Cancelar memoria de comercio ('no') tras corregir categoría",
            "ejecutar": lambda: p8_caso_5c(datos),
            "esperado": "Respuesta cancelación:\nListo, solo esta vez.\nMemoria guardada: False",
        },
        {
            "id": "P8.5d", "punto": "Punto 8", "match": "exacto",
            "nombre": "Confirmar memoria y aplicar a movimientos anteriores ('sí' y 'sí')",
            "ejecutar": lambda: p8_caso_5d(datos),
            "esperado": (
                "Propuesta anteriores:\n"
                "Listo, lo voy a recordar.\n"
                "¿Paso también el gasto anterior de _kiosco_ a Supermercado?\n"
                "Confirmación anteriores:\n"
                "Listo, pasé 1 gasto a Supermercado.\n"
                "Categoría anterior final: Supermercado"
            ),
        },
        {
            "id": "P8.6", "punto": "Punto 8", "match": "exacto",
            "nombre": "Registrar un gasto y decir 'fue con Santander', verificar billetera y los dos saldos",
            "ejecutar": lambda: p8_caso_6(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Lo cambio de Galicia a Santander?\n"
                "Confirmación:\n"
                "Listo, quedó $5.000 en Kiosco desde Santander.\n"
                "Billetera Santander: True | Saldo Galicia revertido: True | "
                "Saldo Santander descontado: True"
            ),
        },
        {
            "id": "P8.7", "punto": "Punto 8", "match": "exacto",
            "nombre": "Registrar un gasto y decir 'fue ayer', verificar la fecha",
            "ejecutar": lambda: p8_caso_7(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Lo cambio de hoy a ayer?\n"
                "Confirmación:\n"
                "Listo, quedó $5.000 en Kiosco desde Galicia (ayer).\n"
                "Fecha ayer: True"
            ),
        },
        {
            "id": "P8.8", "punto": "Punto 8", "match": "exacto",
            "nombre": "Corregir dos campos en un mismo mensaje (monto y billetera)",
            "ejecutar": lambda: p8_caso_8(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Lo dejo en $3.000 en Kiosco desde Santander?\n"
                "Confirmación:\n"
                "Listo, quedó $3.000 en Kiosco desde Santander.\n"
                "Monto 3000: True | Santander: True"
            ),
        },
        {
            "id": "P8.9", "punto": "Punto 8", "match": "exacto",
            "nombre": "Intentar deshacer una cuota de tarjeta y verificar que se rechaza",
            "ejecutar": lambda: p8_caso_9(datos),
            "esperado": "Las cuotas de tarjeta no se borran por acá. Borrala en miargentum.com",
        },
        {
            "id": "P8.10", "punto": "Punto 8", "match": "exacto",
            "nombre": "Intentar corregir pasado el plazo (>30 min)",
            "ejecutar": lambda: p8_caso_10(datos),
            "esperado": "Pasaron más de 30 minutos. Cambialo en miargentum.com",
        },
        {
            "id": "P8.11", "punto": "Punto 8", "match": "exacto",
            "nombre": "Con propuesta pendiente, 'no, fue en Santander' corrige propuesta y no movimiento anterior",
            "ejecutar": lambda: p8_caso_11(datos),
            "esperado": (
                "Respuesta:\n"
                "¿Lo cambio de Galicia a Santander?\n"
                "Movimiento anterior intacto en Efectivo Pesos: True"
            ),
        },
        {
            "id": "P9.1", "punto": "Punto 9A", "match": "contiene",
            "nombre": "gasté 30000 con la Amex: resuelve la tarjeta única, no descuenta saldo, crea una cuota",
            "ejecutar": lambda: p9_caso_1(datos),
            "esperado": "Saldo intacto: True | Es padre: True | Cuotas creadas: True",
        },
        {
            "id": "P9.2", "punto": "Punto 9A", "match": "exacto",
            "nombre": "gasté 30000 con la Visa: pregunta cuál de las dos",
            "ejecutar": lambda: p9_caso_2(datos),
            "esperado": (
                "¿Con qué tarjeta fue?\n"
                "1. Visa Galicia •••• 1506\n"
                "2. Visa Santander •••• 5077"
            ),
        },
        {
            "id": "P9.3", "punto": "Punto 9A", "match": "contiene",
            "nombre": "gasté 30000 con la del Santander: resuelve la 5077",
            "ejecutar": lambda: p9_caso_3(datos),
            "esperado": "con la Visa Santander •••• 5077",
        },
        {
            "id": "P9.4", "punto": "Punto 9A", "match": "contiene",
            "nombre": "compré una tele en 12 cuotas de 80000: propone 12 cuotas de 80.000, total 960.000",
            "ejecutar": lambda: p9_caso_4(datos),
            "esperado": (
                "12 cuotas de $80.000 en Equipos con la Visa Galicia •••• 1506 "
                "($960.000)."
            ),
        },
        {
            "id": "P9.5", "punto": "Punto 9A", "match": "contiene",
            "nombre": "gasté 80000 en 12 cuotas: propone 12 cuotas de 6.666,67, total 80.000",
            "ejecutar": lambda: p9_caso_5(datos),
            "esperado": (
                "12 cuotas de $6.666,67 en Otros con la Visa Galicia •••• 1506 "
                "($80.000)."
            ),
        },
        {
            "id": "P9.6", "punto": "Punto 9A", "match": "contiene",
            "nombre": "gasté 5000 con Galicia: sigue siendo la billetera, no la tarjeta",
            "ejecutar": lambda: p9_caso_6(datos),
            "esperado": "desde Galicia",
        },
        {
            "id": "P9.7", "punto": "Punto 9A", "match": "contiene",
            "nombre": "gasté 5000 con la Visa del Galicia: resuelve la tarjeta 1506",
            "ejecutar": lambda: p9_caso_7(datos),
            "esperado": "con la Visa Galicia •••• 1506",
        },
        {
            "id": "P9.8", "punto": "Punto 9A", "match": "exacto",
            "nombre": "pagué el resumen de la tarjeta: explica que se hace desde la web, no registra nada",
            "ejecutar": lambda: p9_caso_8(datos),
            "esperado": (
                "El pago del resumen se carga en miargentum.com, no por acá. "
                "| Txs creadas: 0"
            ),
        },
        {
            "id": "P9.9", "punto": "Punto 9A", "match": "contiene",
            "nombre": "gasté 5000 con la tarjeta de débito: NO es crédito",
            "ejecutar": lambda: p9_caso_9(datos),
            "esperado": "desde Galicia",
        },
        {
            "id": "P9.10", "punto": "Punto 9A", "match": "contiene",
            "nombre": "Registrar un consumo con tarjeta y deshacerlo: verifica que se borren padre, grupo y cuotas",
            "ejecutar": lambda: p9_caso_10(datos),
            "esperado": "Listo, lo borré.\nPadres restantes: 0",
        },
        {
            "id": "P9.11", "punto": "Punto 9A", "match": "exacto",
            "nombre": "Un usuario sin tarjetas dice 'gasté 5000 con la tarjeta': mensaje claro",
            "ejecutar": lambda: p9_caso_11(datos),
            "esperado": (
                "No tenés tarjetas de crédito cargadas.\n"
                "Agregala en miargentum.com o decime de qué cuenta salió y lo anoto como gasto."
            ),
        },
        {
            "id": "P9.12", "punto": "Punto 9A", "match": "contiene",
            "nombre": "gasté 3 gambas en el remis: ya no debe interpretarse como 3.000",
            "ejecutar": lambda: p9_caso_12(datos),
            "esperado": "No interpretado como 3000: True",
        },
        {
            "id": "P9B.1", "punto": "Punto 9B", "match": "contiene",
            "nombre": "pasé 10 mil de Galicia a Santander: crea transferencia, ajusta los dos saldos, cero gastos",
            "ejecutar": lambda: p9b_caso_1(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
        },
        {
            "id": "P9B.2", "punto": "Punto 9B", "match": "exacto",
            "nombre": "me transferí 20000 a Santander: pregunta el origen o usa la principal, según corresponda",
            "ejecutar": lambda: p9b_caso_2(datos),
            "esperado": (
                "¿Desde qué cuenta pasaste los $20.000 a Santander?\n"
                "1. Galicia\n2. Efectivo Pesos"
            ),
        },
        {
            "id": "P9B.3", "punto": "Punto 9B", "match": "contiene",
            "nombre": (
                "saqué 50000 del cajero: pregunta de qué cuenta y con 1 transfiere "
                "al efectivo, cero gastos"
            ),
            "ejecutar": lambda: p9b_caso_3(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
        },
        {
            "id": "P9B.4", "punto": "Punto 9B", "match": "contiene",
            "nombre": "saqué 50000 del cajero con un usuario sin billetera de efectivo: mensaje claro, no registra",
            "ejecutar": lambda: p9b_caso_4(datos),
            "esperado": (
                "No tenés una cuenta de efectivo en pesos. "
                "Creala en miargentum.com | No registra: True"
            ),
        },
        {
            "id": "P9B.5", "punto": "Punto 9B", "match": "contiene",
            "nombre": "compré 100 dólares a 1500: transfiere 150.000 pesos y suma 100 dólares",
            "ejecutar": lambda: p9b_caso_5(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
        },
        {
            "id": "P9B.6", "punto": "Punto 9B", "match": "exacto",
            "nombre": "compré 100 dólares: pregunta la cotización o los pesos",
            "ejecutar": lambda: p9b_caso_6(datos),
            "esperado": "¿A cuánto compraste o cuántos pesos pagaste?",
        },
        {
            "id": "P9B.7", "punto": "Punto 9B", "match": "contiene",
            "nombre": "vendí 50 dólares a 1450: transfiere al revés",
            "ejecutar": lambda: p9b_caso_7(datos),
            "esperado": "Saldos ajustados: True",
        },
        {
            "id": "P9B.8", "punto": "Punto 9B", "match": "contiene",
            "nombre": "compré 100 dólares a 5: advierte que la cotización es absurda",
            "ejecutar": lambda: p9b_caso_8(datos),
            "esperado": "¿$5 por dólar? Hoy el dólar está a",
        },
        {
            "id": "P9B.9", "punto": "Punto 9B", "match": "contiene",
            "nombre": "le transferí 5000 a mi hermano: es un gasto, no una transferencia",
            "ejecutar": lambda: p9b_caso_9(datos),
            "esperado": "Es gasto: True",
        },
        {
            "id": "P9B.10", "punto": "Punto 9B", "match": "contiene",
            "nombre": "gasté 5000 en el kiosco: sigue siendo un gasto",
            "ejecutar": lambda: p9b_caso_10(datos),
            "esperado": "Listo, anoté $5.000 en Kiosco desde Galicia.",
        },
        {
            "id": "P9B.11", "punto": "Punto 9B", "match": "contiene",
            "nombre": "Registrar una transferencia y deshacerla: los dos saldos vuelven",
            "ejecutar": lambda: p9b_caso_11(datos),
            "esperado": "Saldos intactos: True",
        },
        {
            "id": "P9B.12", "punto": "Punto 9B", "match": "exacto",
            "nombre": "Transferencia con origen y destino iguales: se rechaza",
            "ejecutar": lambda: p9b_caso_12(datos),
            "esperado": "Pusiste la misma cuenta de los dos lados. ¿A qué otra cuenta lo pasaste?",
        },
        {
            "id": "P9B.13", "punto": "Punto 9B", "match": "contiene",
            "nombre": "compré 5 dólares y responder 7500: debe registrar 5 dólares a 1.500, no rechazar",
            "ejecutar": lambda: p9b_caso_13(datos),
            "esperado": "Saldos: True",
        },
        {
            "id": "P9B.14", "punto": "Punto 9B", "match": "contiene",
            "nombre": "compré 100 dólares y responder 1500: cotización unitaria",
            "ejecutar": lambda: p9b_caso_14(datos),
            "esperado": "Salen $150.000 de Galicia y entran US$100 a Efectivo Dólares (a $1.500).",
        },
        {
            "id": "P9B.15", "punto": "Punto 9B", "match": "contiene",
            "nombre": "compré 100 dólares y responder 150000: monto total",
            "ejecutar": lambda: p9b_caso_15(datos),
            "esperado": "Salen $150.000 de Galicia y entran US$100 a Efectivo Dólares (a $1.500).",
        },
        {
            "id": "P9B.16", "punto": "Punto 9B", "match": "contiene",
            "nombre": "compré 100 dólares a 15: debe advertir, con la cotización de referencia en el mensaje",
            "ejecutar": lambda: p9b_caso_16(datos),
            "esperado": "¿$15 por dólar? Hoy el dólar está a",
        },
        {
            "id": "P9B.17", "punto": "Punto 9B", "match": "contiene",
            "nombre": "compré 100 dólares a 1500 con la tabla de cotizaciones vacía: no rechaza, pide confirmación",
            "ejecutar": lambda: p9b_caso_17(datos),
            "esperado": "Salen $150.000 de Galicia y entran US$100 a Efectivo Dólares (a $1.500).",
        },
        {
            "id": "P9B.18", "punto": "Punto 9B", "match": "contiene",
            "nombre": "pasé 50000 de Galicia a Efectivo USD: no transfiere 1:1, pregunta cotización o dólares",
            "ejecutar": lambda: p9b_caso_18(datos),
            "esperado": (
                "¿A cuánto compraste o cuántos dólares entraron en Efectivo Dólares? | "
                "Sin acreditar 1a1: True"
            ),
        },
        {
            "id": "P9B.19", "punto": "Punto 9B", "match": "contiene",
            "nombre": "pasé 50 de Efectivo USD a Galicia: no transfiere 1:1, pregunta cotización o pesos",
            "ejecutar": lambda: p9b_caso_19(datos),
            "esperado": (
                "¿A cuánto vendiste o cuántos pesos entraron en Galicia? | "
                "Sin acreditar 1a1: True"
            ),
        },
        {
            "id": "P9B.20", "punto": "Punto 9B", "match": "contiene",
            "nombre": "pasé 15000 de Galicia a Santander: misma moneda funciona directo sin preguntas",
            "ejecutar": lambda: p9b_caso_20(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
        },
        {
            "id": "P10.1", "punto": "Punto 10", "match": "exacto",
            "nombre": "empecé a pagar 5000 de Disney+: pregunta la frecuencia, crea la suscripción, no cobra nada",
            "ejecutar": lambda: p10_caso_1(datos),
            "esperado": "Pregunta frecuencia: True\nPropuesta: True\nConfirmación: True\nSub creada: True\nTxs cobro generadas: 0",
        },
        {
            "id": "P10.2", "punto": "Punto 10", "match": "contiene",
            "nombre": "gasté 5000 en Disney+: sigue siendo un gasto suelto",
            "ejecutar": lambda: p10_caso_2(datos),
            "esperado": "Listo, anoté $5.000 en Streaming desde Galicia.",
        },
        {
            "id": "P10.3", "punto": "Punto 10", "match": "exacto",
            "nombre": "me suscribí a Netflix por 9000 por mes: crea con frecuencia mensual confirmada",
            "ejecutar": lambda: p10_caso_3(datos),
            "esperado": "Propuesta: True\nConfirmación: True\nFrecuencia mensual: True",
        },
        {
            "id": "P10.4", "punto": "Punto 10", "match": "exacto",
            "nombre": "pagué el Spotify: pregunta si es gasto único o suscripción",
            "ejecutar": lambda: p10_caso_4(datos),
            "esperado": "¿Spotify lo pagás todos los meses?",
        },
        {
            "id": "P10.5", "punto": "Punto 10", "match": "exacto",
            "nombre": "me suscribí a ChatGPT por 20 dólares por mes con medio de pago en pesos: crea la suscripción en dólares",
            "ejecutar": lambda: p10_caso_5(datos),
            "esperado": "Propuesta: True\nSub creada: True\nMoneda sub: USD\nMedio de pago pesos: True",
        },
        {
            "id": "P10.6", "punto": "Punto 10", "match": "exacto",
            "nombre": "di de baja Netflix sin tenerla: mensaje claro",
            "ejecutar": lambda: p10_caso_6(datos),
            "esperado": "No tenés ninguna suscripción a Netflix.",
        },
        {
            "id": "P10.7", "punto": "Punto 10", "match": "exacto",
            "nombre": "di de baja la suscripción existente: confirma y la da de baja",
            "ejecutar": lambda: p10_caso_7(datos),
            "esperado": "Pregunta confirmación: True\nConfirmación: True\nEstado final: cancelada",
        },
        {
            "id": "P10.8", "punto": "Punto 10", "match": "exacto",
            "nombre": "aumentó Netflix, ahora son 12000: muestra el precio anterior y el nuevo",
            "ejecutar": lambda: p10_caso_8(datos),
            "esperado": "Propuesta muestra ambos: True\nConfirmación: True\nPrecio en base: 12000.00",
        },
        {
            "id": "P10.9", "punto": "Punto 10", "match": "exacto",
            "nombre": "cuánto gasto en suscripciones: lista y total mensual",
            "ejecutar": lambda: p10_caso_9(datos),
            "esperado": "Lista activa: True\nTotal mensual: True\nSin saldos billetera: True",
        },
        {
            "id": "P10.10", "punto": "Punto 10", "match": "exacto",
            "nombre": "Registrar un gasto igual a una suscripción ya cobrada este período: avisa antes",
            "ejecutar": lambda: p10_caso_10(datos),
            "esperado": "Aviso cobro previo: True\nRegistro tras confirmación: True",
        },
        {
            "id": "P10.11", "punto": "Punto 10", "match": "exacto",
            "nombre": "Crear una suscripción de un servicio que ya tiene activa: avisa",
            "ejecutar": lambda: p10_caso_11(datos),
            "esperado": "Aviso existente: True",
        },
        {
            "id": "P10.12", "punto": "Punto 10", "match": "exacto",
            "nombre": "Un servicio que no está en el catálogo: lo acepta igual",
            "ejecutar": lambda: p10_caso_12(datos),
            "esperado": "Propuesta servicio no-catálogo: True",
        },
        {
            "id": "P11.1", "punto": "Punto 11", "match": "exacto",
            "nombre": "Dos gastos con billeteras distintas nombradas explícitamente",
            "ejecutar": lambda: p11_caso_1(datos),
            "esperado": "Propuesta: True | Confirmacion: True",
        },
        {
            "id": "P11.2", "punto": "Punto 11", "match": "exacto",
            "nombre": "Dos gastos sin billetera, con el usuario teniendo principal",
            "ejecutar": lambda: p11_caso_2(datos),
            "esperado": "Propuesta principal: True",
        },
        {
            "id": "P11.3", "punto": "Punto 11", "match": "exacto",
            "nombre": "Dos gastos sin billetera, sin principal: pregunta una vez",
            "ejecutar": lambda: p11_caso_3(datos),
            "esperado": "Pregunta una vez: True | Propuesta resuelta: True",
        },
        {
            "id": "P11.4", "punto": "Punto 11", "match": "exacto",
            "nombre": "Tres gastos donde solo uno nombra billetera",
            "ejecutar": lambda: p11_caso_4(datos),
            "esperado": "Pregunta faltantes: True | Propuesta mixta: True",
        },
        {
            "id": "P11.5", "punto": "Punto 11", "match": "exacto",
            "nombre": "Un gasto y un ingreso en el mismo mensaje",
            "ejecutar": lambda: p11_caso_5(datos),
            "esperado": "Propuesta signos: True | Confirmacion signos: True",
        },
        {
            "id": "P11.6", "punto": "Punto 11", "match": "exacto",
            "nombre": "Un lote con un consumo de tarjeta y un gasto normal",
            "ejecutar": lambda: p11_caso_6(datos),
            "esperado": "Lote tarjeta y gasto: True",
        },
        {
            "id": "P11.7", "punto": "Punto 11", "match": "exacto",
            "nombre": "Doce movimientos: rechaza con mensaje claro",
            "ejecutar": lambda: p11_caso_7(datos),
            "esperado": "Rechazo tope: True",
        },
        {
            "id": "P11.8", "punto": "Punto 11", "match": "exacto",
            "nombre": "Un lote donde una operación está en otra moneda sin billetera de esa moneda",
            "ejecutar": lambda: p11_caso_8(datos),
            "esperado": "Aviso descarte y propuesta: True",
        },
        {
            "id": "P11.9", "punto": "Punto 11", "match": "exacto",
            "nombre": "Un lote con dos movimientos idénticos",
            "ejecutar": lambda: p11_caso_9(datos),
            "esperado": "Deteccion duplicado interno: True",
        },
        {
            "id": "P11.10", "punto": "Punto 11", "match": "exacto",
            "nombre": "Un lote seguido de 'borrá eso'",
            "ejecutar": lambda: p11_caso_10(datos),
            "esperado": "Propuesta deshacer lote: True | Confirmacion deshacer lote: True",
        },
        {
            "id": "P11.11", "punto": "Punto 11", "match": "exacto",
            "nombre": "Lote de 3 gastos en un solo mensaje: confirma, crea 3 txs, valida accion_ejecutada > 100 caracteres",
            "ejecutar": lambda: p11_caso_11(datos),
            "esperado": "Txs creadas: 3 | Accion len ok: True | Sin error: True",
        },
        {
            "id": "P11.12", "punto": "Punto 11", "match": "exacto",
            "nombre": "Falso positivo corregido: lote con 'pasé al kiosco' no bloquea por transferencia y registra movimientos",
            "ejecutar": lambda: p11_caso_12(datos),
            "esperado": "Falso positivo evitado: True | Txs creadas ok: True",
        },
        {
            "id": "P11.13", "punto": "Punto 11", "match": "exacto",
            "nombre": "Pago a un tercero dentro de un lote no bloquea: se registran los dos gastos",
            "ejecutar": lambda: p11_caso_13(datos),
            "esperado": "No bloqueado: True | Dos egresos: True | Montos 3000 y 5000: True",
        },
        {
            "id": "P12.1", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_balance: 'cuál es mi balance' detecta intent y devuelve balance real del dashboard",
            "ejecutar": lambda: p12_caso_1(datos),
            "esperado": "Intent: consultar_balance | Datos reales: True",
        },
        {
            "id": "P12.2", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_cotizacion: 'a cuánto está el dólar' detecta intent y devuelve cotizaciones reales",
            "ejecutar": lambda: p12_caso_2(datos),
            "esperado": "Intent: consultar_cotizacion | Cotizacion fija ok: True",
        },
        {
            "id": "P12.3", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_saldo: 'cuánto tengo' detecta intent y devuelve saldo real del dashboard",
            "ejecutar": lambda: p12_caso_3(datos),
            "esperado": "Intent: consultar_saldo | Datos reales: True",
        },
        {
            "id": "P12.4", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_proyeccion: 'cuál es mi proyección financiera' detecta intent y devuelve proyección real",
            "ejecutar": lambda: p12_caso_4(datos),
            "esperado": "Intent: consultar_proyeccion | Proyeccion ok: True",
        },
        {
            "id": "P12.5", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_saldo: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_5(datos),
            "esperado": "Intent: consultar_saldo | Falla manejada: True",
        },
        {
            "id": "P12.6", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_balance: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_6(datos),
            "esperado": "Intent: consultar_balance | Falla manejada: True",
        },
        {
            "id": "P12.7", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_proyeccion: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_7(datos),
            "esperado": "Intent: consultar_proyeccion | Falla manejada: True",
        },
        {
            "id": "P12.8", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_cotizacion: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_8(datos),
            "esperado": "Intent: consultar_cotizacion | Falla manejada: True",
        },
        {
            "id": "P12.9", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_cotizacion: cotizaciones vacías devuelve mensaje amigable",
            "ejecutar": lambda: p12_caso_9(datos),
            "esperado": "Intent: consultar_cotizacion | Falla manejada: True",
        },
        {
            "id": "P12.10", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_meta: 'cómo va mi meta' detecta intent y devuelve metas reales",
            "ejecutar": lambda: p12_caso_10(datos),
            "esperado": "Intent: consultar_meta | Datos reales: True",
        },
        {
            "id": "P12.11", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_presupuesto: 'cómo va mi presupuesto' detecta intent y devuelve presupuestos reales",
            "ejecutar": lambda: p12_caso_11(datos),
            "esperado": "Intent: consultar_presupuesto | Datos reales: True",
        },
        {
            "id": "P12.12", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_meta: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_12(datos),
            "esperado": "Intent: consultar_meta | Falla manejada: True",
        },
        {
            "id": "P12.13", "punto": "Punto 12", "match": "exacto",
            "nombre": "consultar_presupuesto: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_13(datos),
            "esperado": "Intent: consultar_presupuesto | Falla manejada: True",
        },
        {
            "id": "P13.1", "punto": "Punto 13", "match": "exacto",
            "nombre": "consultar_gastos: 'cuánto gasté hoy' detecta intent y calcula gastos de hoy",
            "ejecutar": lambda: p13_caso_1(datos),
            "esperado": "Intent: consultar_gastos | Respuesta ok: True",
        },
        {
            "id": "P13.2", "punto": "Punto 13", "match": "exacto",
            "nombre": "consultar_gastos: 'cuánto gasté este ciclo' coincide con balance de ciclo",
            "ejecutar": lambda: p13_caso_2(datos),
            "esperado": "Intent: consultar_gastos | Coincide con balance: True",
        },
        {
            "id": "P13.3", "punto": "Punto 13", "match": "exacto",
            "nombre": "consultar_gastos: 'cuánto gasté en pizza esta semana' filtra por descripción",
            "ejecutar": lambda: p13_caso_3(datos),
            "esperado": "Intent: consultar_gastos | Filtro por descripcion: True",
        },
        {
            "id": "P13.4", "punto": "Punto 13", "match": "exacto",
            "nombre": "consultar_gastos: 'cuánto gasté en supermercado el mes pasado' filtra por catálogo",
            "ejecutar": lambda: p13_caso_4(datos),
            "esperado": "Intent: consultar_gastos | Filtro por catalogo: True",
        },
        {
            "id": "P13.5", "punto": "Punto 13", "match": "exacto",
            "nombre": "consultar_gastos: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p13_caso_5(datos),
            "esperado": "Intent: consultar_gastos | Falla manejada: True",
        },
        {
            "id": "P18.1", "punto": "Punto 18", "match": "exacto",
            "nombre": "Consulta permitirse contado: tele de 300.000",
            "ejecutar": lambda: p18_caso_1(datos),
            "esperado": "Intent: puede_permitirse | Respuesta ok: True",
        },
        {
            "id": "P18.2", "punto": "Punto 18", "match": "exacto",
            "nombre": "Consulta permitirse cuotas: celular de 600 mil en 6 cuotas",
            "ejecutar": lambda: p18_caso_2(datos),
            "esperado": "Intent: puede_permitirse | Respuesta ok: True",
        },
        {
            "id": "P18.3", "punto": "Punto 18", "match": "exacto",
            "nombre": "Consulta permitirse cuotas de X: heladera en 12 cuotas de 50 lucas",
            "ejecutar": lambda: p18_caso_3(datos),
            "esperado": "Intent: puede_permitirse | Respuesta ok: True",
        },
        {
            "id": "P18.4", "punto": "Punto 18", "match": "exacto",
            "nombre": "Consulta permitirse sin precio: me lo puedo permitir",
            "ejecutar": lambda: p18_caso_4(datos),
            "esperado": "Intent: puede_permitirse | Respuesta ok: True",
        },
        {
            "id": "P19.1", "punto": "Punto 19", "match": "exacto",
            "nombre": "Ticket único por imagen con propuesta nombrando billetera y confirmación con sí",
            "ejecutar": lambda: p19_caso_1(datos),
            "esperado": "Propuesta billetera nombrada: True | Registrado tras sí: True | Total txs: 1",
        },
        {
            "id": "P19.2", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con 3 movimientos por imagen, propuesta de lote y confirmación con sí",
            "ejecutar": lambda: p19_caso_2(datos),
            "esperado": "Propuesta lote 3: True | Registrados tras sí: True | Total txs: 3",
        },
        {
            "id": "P19.3", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con 3 movimientos donde 1 ya existe, propuesta de 2 y aviso de ya tenías cargado",
            "ejecutar": lambda: p19_caso_3(datos),
            "esperado": "Propuesta 2 movs: True | Linea ya tenias cargado: True | Registrados tras sí: True | Total txs: True",
        },
        {
            "id": "P19.4", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con todos movimientos duplicados, sin propuesta confirmable pendiente",
            "ejecutar": lambda: p19_caso_4(datos),
            "esperado": "Respuesta todos duplicados: True | Sin propuesta pendiente tras sí: True | Creadas: 0",
        },
        {
            "id": "P19.5", "punto": "Punto 19", "match": "exacto",
            "nombre": "Captura con 12 movimientos, propuesta de los primeros 10 y aviso",
            "ejecutar": lambda: p19_caso_5(datos),
            "esperado": "Propuesta 10 movs: True | Aviso tope 12: True",
        },
        {
            "id": "P19.6", "punto": "Punto 19", "match": "exacto",
            "nombre": "Imagen ilegible, texto de reintento y sin propuesta pendiente",
            "ejecutar": lambda: p19_caso_6(datos),
            "esperado": "Texto ilegible: True | Nada pendiente tras sí: True | Creadas: 0",
        },
        {
            "id": "P19.7", "punto": "Punto 19", "match": "exacto",
            "nombre": "Factura de servicio con línea explicativa de no pago",
            "ejecutar": lambda: p19_caso_7(datos),
            "esperado": "Linea si no la pagaste: True | Registrado tras sí: True | Creadas: 1",
        },
        {
            "id": "P19.8", "punto": "Punto 19", "match": "exacto",
            "nombre": "Respuesta no tras propuesta por imagen cancela y no registra nada",
            "ejecutar": lambda: p19_caso_8(datos),
            "esperado": "Respuesta cancelado tras no: True | Creadas: 0",
        },
        {
            "id": "P19.9", "punto": "Punto 19", "match": "exacto",
            "nombre": "Comprobante de transferencia no usa billetera_texto, asume principal Galicia y registra con sí",
            "ejecutar": lambda: p19_caso_9(datos),
            "esperado": "Propuesta Galicia sin Santander con va: True | Registrado tras sí: True | Creadas: 1",
        },
    ]
    catalogo.extend(entradas_p09c(datos))
    catalogo.extend(entradas_p16_p17(datos))
    catalogo.extend(entradas_p18b(datos))
    catalogo.extend(entradas_p19b(datos))
    catalogo.extend(entradas_p20(datos))
    catalogo.extend(entradas_p21(datos))
    catalogo.extend(entradas_p22(datos))
    catalogo.extend(entradas_p23(datos))
    return catalogo

