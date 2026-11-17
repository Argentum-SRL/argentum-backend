"""Pruebas de reglas de texto web (TW1 vectores, TW2 fecha_dm, TW3 limpiar_mensaje_web)."""
from __future__ import annotations

from datetime import date
import pytest

from app.core.reglas_texto_web import (
    LARGO_MAXIMO_TITULO_WEB,
    MAXIMO_RENGLONES_WEB,
    fecha_dm,
    problemas_texto_web,
)
from app.utils.texto import limpiar_mensaje_web

VECTORES_WEB: list[tuple[str, list[str]]] = [
    (
        'Meta creada.',
        [],
    ),
    (
        'Transacción eliminada correctamente',
        [
            'W2_PALABRA:correctamente',
            'W7_SIN_PUNTO:1',
        ],
    ),
    (
        'Rendimiento registrado exitosamente',
        [
            'W2_PALABRA:exitosamente',
            'W7_SIN_PUNTO:1',
        ],
    ),
    (
        'No pudimos cargar las metas. Intentá de nuevo.',
        [
            'W2_PALABRA:intentá',
            'W2_PALABRA:primera persona',
        ],
    ),
    (
        'Por favor, seleccioná una tarjeta y una billetera.',
        [
            'W2_PALABRA:por favor',
        ],
    ),
    (
        'Debe ingresar una TNA válida entre 0.1% y 3000%',
        [
            'W2_PALABRA:válido',
            'W2_PALABRA:deber',
            'W2_PALABRA:TNA',
            'W7_SIN_PUNTO:1',
        ],
    ),
    (
        'Ingresá un monto mayor a 0.',
        [],
    ),
    (
        '"Galicia" archivada.',
        [
            'W3_COMILLAS',
        ],
    ),
    (
        'Billetera Galicia archivada.',
        [],
    ),
    (
        '*Cerraste el ciclo*',
        [
            'W4_MARCAS',
            'W7_SIN_PUNTO:1',
        ],
    ),
    (
        'Listo: 3 movimiento(s) actualizado(s).',
        [
            'W2_PALABRA:listo',
            'W5_PLURAL_PARENTESIS',
        ],
    ),
    (
        'Cálculo listo.',
        [],
    ),
    (
        'El resumen de tu Visa Galicia •••• 1506 cierra el 22/11 y vence el 4/12.',
        [],
    ),
    (
        'Vence el 20/11/2026.',
        [
            'W9_FECHA',
        ],
    ),
    (
        'Vence el 04/12.',
        [
            'W9_FECHA',
        ],
    ),
    (
        'Vence el 4/12.',
        [],
    ),
    (
        'Vence el 9 de octubre.',
        [
            'W9_FECHA',
        ],
    ),
    (
        'Pagá en https://miargentum.com',
        [
            'W7_SIN_PUNTO:1',
            'W11_LINK',
        ],
    ),
    (
        'Hola 👍',
        [
            'W7_SIN_PUNTO:1',
            'W10_EMOJI',
        ],
    ),
    (
        '• Café',
        [
            'W7_SIN_PUNTO:1',
            'W13_LISTA',
        ],
    ),
    (
        '1) Galicia.',
        [
            'W13_LISTA',
        ],
    ),
    (
        (
            'Terminó tu mes: te sobraron $80.000.\n'
            'Entraron $500.000 y gastaste $420.000.\n'
            'Lo que más pesó: Comida ($95.000).\n'
            'Gastos chicos que se repiten:\n'
            '- Café: $18.000 en 12 compras\n'
            '- Kiosco: $9.000 en 7 compras'
        ),
        [],
    ),
    (
        'Tenés {x}.',
        [
            'W8_SIN_RELLENAR',
        ],
    ),
    (
        'Te va a llegar un código a tu email.',
        [],
    ),
    (
        'Contraseña actualizada. Ya podés entrar con la nueva.',
        [],
    ),
    (
        'Se cambió el email de tu cuenta.\nSi no fuiste vos, cambiá tu contraseña ya.',
        [],
    ),
    (
        'No se pudo eliminar la meta. Probá de nuevo.',
        [],
    ),
    (
        'Tu billetera Mercado Pago se quedó sin saldo.',
        [],
    ),
    (
        'Ya usaste el 80% del presupuesto Comida: $80.000 de $100.000.',
        [],
    ),
    (
        'El precio de contado excede el límite permitido',
        [
            'W2_PALABRA:exceder',
            'W7_SIN_PUNTO:1',
        ],
    ),
    (
        'Tu cuenta ha sido eliminada exitosamente.',
        [
            'W2_PALABRA:exitosamente',
            'W2_PALABRA:pasiva',
        ],
    ),
    (
        'Si tienes dudas, escribe.',
        [
            'W2_PALABRA:tuteo',
        ],
    ),
    (
        'Hoy se cobra Netflix: US$10.',
        [],
    ),
    (
        'Gasto de ARS 500.',
        [
            'W2_PALABRA:ARS',
        ],
    ),
    (
        '¿Te llegó el reintegro de $25.000 de Súper?',
        [],
    ),
    (
        'Completá los datos requeridos',
        [
            'W2_PALABRA:requerido',
            'W7_SIN_PUNTO:1',
        ],
    ),
    (
        'No se puede eliminar una tarjeta con transacciones. Archivala.',
        [],
    ),
    (
        'Listo, no lo vamos a mostrar más.',
        [
            'W2_PALABRA:listo',
            'W2_PALABRA:primera persona',
        ],
    ),
    (
        "Es O'Higgins.",
        [],
    ),
    (
        'Saldo: undefined.',
        [
            'W8_SIN_RELLENAR',
        ],
    ),
    (
        'Cierre el 3 de diciembre.',
        [
            'W9_FECHA',
        ],
    ),
    (
        'Anteayer pagaste.',
        [
            'W9_FECHA',
        ],
    ),
    (
        (
            'Esta línea de prueba es bastante larga y tien'
            'e que medir justo noventa caracteres en tota.'
        ),
        [],
    ),
    (
        (
            'Esta línea de prueba es bastante larga y tien'
            'e que medir justo noventa caracteres en totas.'
        ),
        [
            'W1_TITULO_LARGO:91',
        ],
    ),
    (
        'Uno.\nDos.\nTres.\nCuatro.\nCinco.\nSeis.\nSiete.',
        [],
    ),
    (
        'Uno.\nDos.\nTres.\nCuatro.\nCinco.\nSeis.\nSiete.\nOcho.',
        [
            'W12_RENGLONES',
        ],
    ),
    (
        (
            'Tu cuenta está suspendida. Si creés que es un error, '
            'escribí a srlargentum@gmail.com.'
        ),
        [],
    ),
    (
        'Entrá a miargentum.com.',
        [
            'W11_LINK',
        ],
    ),
    (
        'Escribí a x@y.com.',
        [],
    ),
    (
        'Escribí a x@y.com o entrá a www.miargentum.com.',
        [
            'W11_LINK',
        ],
    ),
    (
        'Algo salió mal de nuestro lado.',
        [
            'W2_PALABRA:primera persona',
        ],
    ),
    (
        'Revisá nuestras sugerencias.',
        [
            'W2_PALABRA:primera persona',
        ],
    ),
    (
        'Lo hacemos nosotros.',
        [
            'W2_PALABRA:primera persona',
        ],
    ),
    (
        'Algo salió mal. Probá de nuevo en unos minutos.',
        [],
    ),
]


def test_constantes_reglas_web():
    assert LARGO_MAXIMO_TITULO_WEB == 90
    assert MAXIMO_RENGLONES_WEB == 7


@pytest.mark.parametrize("texto,esperado", VECTORES_WEB)
def test_tw1_vectores_reglas_web(texto: str, esperado: list[str]):
    assert problemas_texto_web(texto) == esperado


def test_tw2_fecha_dm():
    assert fecha_dm(date(2026, 12, 4)) == "4/12"
    assert fecha_dm(date(2026, 11, 22)) == "22/11"
    assert fecha_dm(date(2026, 10, 9)) == "9/10"
    assert fecha_dm(date(2027, 1, 1)) == "1/01"


def test_tw3_limpiar_mensaje_web():
    assert limpiar_mensaje_web("Meta creada.") == "Meta creada."
    resumen_sucio = (
        "Terminó tu mes: te sobraron $80.000.\n"
        "Entraron $500.000 y gastaste $420.000.\n\n"
        "Gastos chicos que se repiten:\n"
        "- Café: $18.000 en 12 compras"
    )
    resumen_limpio = (
        "Terminó tu mes: te sobraron $80.000.\n"
        "Entraron $500.000 y gastaste $420.000.\n"
        "Gastos chicos que se repiten:\n"
        "- Café: $18.000 en 12 compras"
    )
    assert limpiar_mensaje_web(resumen_sucio) == resumen_limpio
    assert (
        limpiar_mensaje_web(
            "Si no fuiste vos, cambiala ya: https://miargentum.com/auth/x"
        )
        == "Si no fuiste vos, cambiala ya."
    )
    assert (
        limpiar_mensaje_web("Mirá [acá](https://miargentum.com) los datos.")
        == "Mirá acá los datos."
    )
    assert limpiar_mensaje_web("") == ""
    assert limpiar_mensaje_web(None) == ""
    assert limpiar_mensaje_web("Hola   mundo.\n   \n- a") == "Hola mundo.\n- a"
