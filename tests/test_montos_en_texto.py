"""
tests/test_montos_en_texto.py — Tests unitarios de montos_de_dinero_en_texto (Decisión C).
"""
import pytest
from app.routers.whatsapp.parsers import montos_de_dinero_en_texto


def test_caso_1_lote_con_tres_montos():
    texto = "gasté 2.900 pesos en Uber y 16.900 pesos de los chinos. Mi mamá me transfirió 300.000 pesos"
    montos = montos_de_dinero_en_texto(texto)
    assert len(montos) == 3


def test_caso_2_fecha_y_monto():
    texto = "el 27/09 gasté $5000"
    montos = montos_de_dinero_en_texto(texto)
    assert len(montos) == 1


def test_caso_3_cuotas_de():
    texto = "pagué $10.000 en 3 cuotas de $3.333"
    montos = montos_de_dinero_en_texto(texto)
    assert len(montos) == 1


def test_caso_4_lucas():
    texto = "gasté 5 lucas en el kiosco"
    montos = montos_de_dinero_en_texto(texto)
    assert len(montos) == 1


def test_caso_5_dolares():
    texto = "compré 100 dólares"
    montos = montos_de_dinero_en_texto(texto)
    assert len(montos) == 1


def test_caso_6_sin_marca_de_dinero():
    texto = "el 18 de septiembre gasté 8.600 en Uber"
    montos = montos_de_dinero_en_texto(texto)
    assert len(montos) == 0


def test_caso_7_varios_signos_pesos():
    texto = "gasté $1400 en didi, $27.630 en Rappi y $5.000 en Didi Food"
    montos = montos_de_dinero_en_texto(texto)
    assert len(montos) == 3
