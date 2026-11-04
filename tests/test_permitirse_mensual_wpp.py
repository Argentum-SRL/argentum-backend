# -*- coding: utf-8 -*-
"""
tests/test_permitirse_mensual_wpp.py — Pruebas para detección y respuestas de modo mensual en WhatsApp.
"""
from decimal import Decimal

from app.routers.whatsapp.handlers_permitirse import (
    armar_respuesta_permitirse,
    detectar_consulta_permitirse,
)


def test_detectar_mensual_gimnasio_por_mes():
    # "¿me puedo permitir un gimnasio de 40.000 por mes?" → modo "mensual", precio 40.000, cantidad_cuotas 1
    res = detectar_consulta_permitirse("¿me puedo permitir un gimnasio de 40.000 por mes?")
    assert res is not None
    assert res["falta"] is None
    assert res["modo"] == "mensual"
    assert res["precio"] == Decimal("40000")
    assert res["cantidad_cuotas"] == 1
    assert res["tiene_interes"] is False
    assert res["tna"] is None
    assert res["nota_interes"] is False


def test_detectar_mensual_20_lucas_todos_los_meses():
    # "¿me puedo permitir algo de 20 lucas todos los meses?" → mensual, 20.000
    res = detectar_consulta_permitirse("¿me puedo permitir algo de 20 lucas todos los meses?")
    assert res is not None
    assert res["falta"] is None
    assert res["modo"] == "mensual"
    assert res["precio"] == Decimal("20000")
    assert res["cantidad_cuotas"] == 1


def test_detectar_12_cuotas_de_10000_por_mes():
    # "¿me puedo permitir 12 cuotas de 10.000 por mes?" → cuotas, 12, precio 120.000
    res = detectar_consulta_permitirse("¿me puedo permitir 12 cuotas de 10.000 por mes?")
    assert res is not None
    assert res["falta"] is None
    assert res["modo"] == "cuotas"
    assert res["cantidad_cuotas"] == 12
    assert res["precio"] == Decimal("120000")


def test_detectar_tele_300000_contado():
    # "¿me puedo permitir una tele de 300.000?" → contado (igual que hoy)
    res = detectar_consulta_permitirse("¿me puedo permitir una tele de 300.000?")
    assert res is not None
    assert res["falta"] is None
    assert res["modo"] == "contado"
    assert res["precio"] == Decimal("300000")
    assert res["cantidad_cuotas"] == 1


def test_detectar_mensual_al_mes():
    res = detectar_consulta_permitirse("¿me alcanza para 50 lucas al mes?")
    assert res is not None
    assert res["modo"] == "mensual"
    assert res["precio"] == Decimal("50000")
    assert res["cantidad_cuotas"] == 1


def test_detectar_mensual_cada_mes():
    res = detectar_consulta_permitirse("¿me puedo permitir 20000 cada mes?")
    assert res is not None
    assert res["modo"] == "mensual"
    assert res["precio"] == Decimal("20000")
    assert res["cantidad_cuotas"] == 1


def test_detectar_mensual_palabra_mensual():
    res = detectar_consulta_permitirse("¿me puedo permitir un curso de 80.000 mensual?")
    assert res is not None
    assert res["modo"] == "mensual"
    assert res["precio"] == Decimal("80000")
    assert res["cantidad_cuotas"] == 1


def test_respuesta_mensual_bien():
    resultado = {
        "titulo": "Te entra bien",
        "linea_principal": "Cada mes te quedarían al menos $40.000 después de tus gastos de siempre.",
    }
    consulta = {
        "falta": None,
        "precio": Decimal("40000"),
        "modo": "mensual",
        "cantidad_cuotas": 1,
        "tiene_interes": False,
        "tna": None,
        "nota_interes": False,
    }
    resp = armar_respuesta_permitirse(resultado, consulta)
    assert resp == "Te entra bien.\nCada mes te quedarían al menos $40.000 después de tus gastos de siempre."
