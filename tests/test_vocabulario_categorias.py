# -*- coding: utf-8 -*-
"""
tests/test_vocabulario_categorias.py - Tests T.4 para el rescate determinístico por vocabulario.
"""
import pytest
from app.core.vocabulario_categorias import (
    aplicar_rescate_a_entidades,
    aplicar_rescate_si_corresponde,
    rescatar_categoria,
)


@pytest.mark.parametrize(
    "texto,tipo,cat_ia,esperado",
    [
        ("gasté 800 en panadería", "egreso", "Otros", "Panadería"),
        ("pan 2000", "egreso", "Alimentación", "Panadería"),
        ("pollo en la granja", "egreso", "Otros", "Carnicería"),
        ("fui al chino", "egreso", "Otros", "Supermercado"),
        ("fiambrería 4000", "egreso", "Otros", "Alimentación"),
        ("me corté el pelo, 15000", "egreso", "Cuidado personal", "Peluquería y belleza"),
        ("perfume 30000", "egreso", "Otros", "Perfumería"),
        ("gasté 5000 en Disney+", "egreso", "Otros", "Streaming"),
        ("chatgpt 20 dólares", "egreso", "Otros", "Apps y programas"),
        ("compré una tele", "egreso", "Otros", "Equipos"),
        ("pagué el monotributo", "egreso", "Otros", "Monotributo"),
        ("pagué la patente", "egreso", "Otros", "Patente"),
        ("pagué el ABL", "egreso", "Otros", "ABL e inmobiliario"),
        ("le pagué a la niñera", "egreso", "Otros", "Servicio doméstico"),
        ("le transferí 3000 a Manuel", "egreso", "Otros", "Pagos a personas"),
        ("Manuel me transfirió 3000", "ingreso", "Otros", "Cobros de personas"),
        ("le pasé 20000 a mi vieja", "egreso", "Otros", "Ayuda familiar"),
        ("mi mamá me pasó 100000", "ingreso", "Otros", "Ayuda familiar"),
        ("Transferencia a mi hermano", "egreso", "Otros", "Pagos a personas"),
        ("cobré la jubilación", "ingreso", "Otros", "Jubilación o pensión"),
        ("cobré la AUH", "ingreso", "Otros", "AUH y asignaciones"),
        ("me dejaron 5000 de propina", "ingreso", "Otros", "Bonos y extras"),
        # Sin cambio
        ("le transferí 3000 a Manuel por el budín", "egreso", "Panadería", "Panadería"),
        ("pan 2000", "egreso", "Kiosco", "Kiosco"),
        ("pantalón 20000", "egreso", "Otros", "Otros"),
        ("clase de chino", "egreso", "Otros", "Otros"),
        ("compré papas", "egreso", "Otros", "Otros"),
        ("pagué las facturas", "egreso", "Otros", "Otros"),
        ("after office con los chicos", "egreso", "Otros", "Otros"),
        ("le transferí 3000 a Claude", "egreso", "Otros", "Pagos a personas"),
    ],
)
def test_t4_rescatar_categoria_casos(texto, tipo, cat_ia, esperado):
    resultado = rescatar_categoria(texto, tipo=tipo, categoria_ia=cat_ia)
    assert resultado == esperado, f"Para '{texto}' ({tipo}, {cat_ia}): esperado '{esperado}', obtenido '{resultado}'"


def test_t4_lote_con_transacciones_adicionales():
    mensaje = "le arreglé la compu a una amiga y compré un maple de huevos"
    entidades = {
        "tipo": "ingreso",
        "categoria": "Otros",
        "descripcion": "Arreglo de compu",
        "transacciones_adicionales": [
            {
                "tipo": "egreso",
                "categoria": "Otros",
                "descripcion": "Maple de huevos",
                "monto": 2500,
            }
        ],
    }

    aplicar_rescate_a_entidades(entidades, mensaje)

    # La transacción adicional solo mira su descripción ("Maple de huevos"):
    # Como "Maple de huevos" no activa ninguna regla de rescate, queda en "Otros"
    tx_adicional = entidades["transacciones_adicionales"][0]
    assert tx_adicional["categoria"] == "Otros"


def test_t4_decision_19_rescate_none():
    assert rescatar_categoria("fue con Galicia", tipo="egreso", categoria_ia=None) is None
    assert rescatar_categoria("le transferí 3000 a Manuel", tipo="egreso", categoria_ia=None) == "Pagos a personas"


def test_t4_decision_19_aplicar_rescate_si_corresponde():
    # Con intent "corregir", entidades {"categoria": None, "tipo": "egreso"} y mensaje "no, fue con Galicia": la categoría sigue None
    res_corregir = {
        "intent": "corregir",
        "entidades": {"categoria": None, "tipo": "egreso"},
    }
    aplicar_rescate_si_corresponde(res_corregir, "no, fue con Galicia")
    assert res_corregir["entidades"]["categoria"] is None

    # Con intent "consultar_balance", entidades {"categoria": "Otros", "tipo": "egreso"} y mensaje "cuánto le pasé a mi vieja": sigue "Otros"
    res_balance = {
        "intent": "consultar_balance",
        "entidades": {"categoria": "Otros", "tipo": "egreso"},
    }
    aplicar_rescate_si_corresponde(res_balance, "cuánto le pasé a mi vieja")
    assert res_balance["entidades"]["categoria"] == "Otros"

    # Con intent "registrar_transaccion", entidades {"categoria": "Otros", "tipo": "egreso", "descripcion": "Transferencia a Manuel"} y mensaje "le transferí 3000 a Manuel": pasa a "Pagos a personas"
    res_registrar = {
        "intent": "registrar_transaccion",
        "entidades": {"categoria": "Otros", "tipo": "egreso", "descripcion": "Transferencia a Manuel"},
    }
    aplicar_rescate_si_corresponde(res_registrar, "le transferí 3000 a Manuel")
    assert res_registrar["entidades"]["categoria"] == "Pagos a personas"

