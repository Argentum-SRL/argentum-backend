import pytest
from decimal import Decimal

from app.schemas.tools import InstallmentConvenienceRequest
from app.services.tools_service import calcular_conveniencia_cuotas
from app.utils.tasas import tna_a_mensual


def test_tna_a_mensual():
    """TC.1: tna_a_mensual(60) == Decimal('0.05') y tna_a_mensual(12) == Decimal('0.01')."""
    assert tna_a_mensual(60) == Decimal("0.05")
    assert tna_a_mensual(12) == Decimal("0.01")


def test_conveniencia_cuotas_con_billetera_inversion_60():
    """
    TC.1: precio_contado 100.000, 6 cuotas, sin interés y precio_total_cuotas 100.000,
    inflación 0 y tna_billetera_inversion 60:
    resultado 'conviene_cuotas', saldo_final_inversion 20644.35, remanente_vpn 20644.35,
    ahorro_real 20644.35, porcentaje_ahorro 20.64.
    """
    req = InstallmentConvenienceRequest(
        precio_contado=100000,
        cantidad_cuotas=6,
        tiene_interes=False,
        precio_total_cuotas=100000,
        inflacion_mensual=0,
        tna_billetera_inversion=60,
    )
    res = calcular_conveniencia_cuotas(req)
    assert res["resultado"] == "conviene_cuotas"
    assert res["saldo_final_inversion"] == 20644.35
    assert res["remanente_vpn"] == 20644.35
    assert res["ahorro_real"] == 20644.35
    assert res["porcentaje_ahorro"] == 20.64


def test_conveniencia_cuotas_con_billetera_inversion_12_conviene_contado():
    """
    TC.1: Con precio_total_cuotas 120.000 y tna_billetera_inversion 12:
    'conviene_contado', saldo_final_inversion -16888.29, ahorro_real 16888.29, porcentaje_ahorro 16.89.
    """
    req = InstallmentConvenienceRequest(
        precio_contado=100000,
        cantidad_cuotas=6,
        tiene_interes=False,
        precio_total_cuotas=120000,
        inflacion_mensual=0,
        tna_billetera_inversion=12,
    )
    res = calcular_conveniencia_cuotas(req)
    assert res["resultado"] == "conviene_contado"
    assert res["saldo_final_inversion"] == -16888.29
    assert res["ahorro_real"] == 16888.29
    assert res["porcentaje_ahorro"] == 16.89


def test_conveniencia_cuotas_con_billetera_inversion_12_conviene_cuotas():
    """
    TC.1: Con precio_total_cuotas 100.600 y tna 12:
    'conviene_cuotas', saldo_final_inversion 3003.23, porcentaje_ahorro 3.0.
    """
    req = InstallmentConvenienceRequest(
        precio_contado=100000,
        cantidad_cuotas=6,
        tiene_interes=False,
        precio_total_cuotas=100600,
        inflacion_mensual=0,
        tna_billetera_inversion=12,
    )
    res = calcular_conveniencia_cuotas(req)
    assert res["resultado"] == "conviene_cuotas"
    assert res["saldo_final_inversion"] == 3003.23
    assert res["porcentaje_ahorro"] == 3.0


def test_conveniencia_cuotas_sin_billetera_inversion_inflacion_2():
    """
    TC.1: Sin tna_billetera_inversion, con precio_total_cuotas 100.000 en 6 cuotas e inflación 2:
    el mismo resultado que da el código sin billetera de inversión (resultado 'conviene_cuotas' y ahorro_real 6642.82).
    """
    req = InstallmentConvenienceRequest(
        precio_contado=100000,
        cantidad_cuotas=6,
        tiene_interes=False,
        precio_total_cuotas=100000,
        inflacion_mensual=2,
        tna_billetera_inversion=None,
    )
    res = calcular_conveniencia_cuotas(req)
    assert res["resultado"] == "conviene_cuotas"
    assert res["ahorro_real"] == 6642.82
    assert res["porcentaje_ahorro"] == 6.64

