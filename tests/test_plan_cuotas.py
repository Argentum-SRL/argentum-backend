"""
tests/test_plan_cuotas.py — Pruebas unitarias de calcular_plan_cuotas (pura).
"""
from decimal import Decimal
import pytest

from app.services.tools_service import calcular_plan_cuotas


def test_plan_cuotas_th8_sin_interes_con_inflacion():
    # (TH.8) calcular_plan_cuotas(120.000, 12, None, [2]x12) -> cuota 10.000, total 120.000, interes 0, valor_presente 105.753 +- 1.
    plan = calcular_plan_cuotas(
        precio=Decimal("120000"),
        cantidad=12,
        tna=None,
        inflacion_mensual=[Decimal("2.0")] * 12,
    )
    assert plan.monto_cuota == Decimal("10000.00")
    assert plan.total == Decimal("120000.00")
    assert plan.interes == Decimal("0.00")
    assert plan.valor_presente is not None
    assert abs(plan.valor_presente - Decimal("105753")) <= Decimal("1")


def test_plan_cuotas_con_tna_60():
    # (300.000, 6, tna 60, None) -> cuota 59.105,24, total 354.631,44, interes 54.631,44, valor_presente None.
    plan = calcular_plan_cuotas(
        precio=Decimal("300000"),
        cantidad=6,
        tna=Decimal("60"),
        inflacion_mensual=None,
    )
    assert plan.monto_cuota == Decimal("59105.24")
    assert plan.total == Decimal("354631.44")
    assert plan.interes == Decimal("54631.44")
    assert plan.valor_presente is None


def test_plan_cuotas_tna_0():
    # (300.000, 6, tna 0, None) -> cuota 50.000.
    plan = calcular_plan_cuotas(
        precio=Decimal("300000"),
        cantidad=6,
        tna=Decimal("0"),
        inflacion_mensual=None,
    )
    assert plan.monto_cuota == Decimal("50000.00")
    assert plan.total == Decimal("300000.00")
    assert plan.interes == Decimal("0.00")
    assert plan.valor_presente is None


def test_plan_cuotas_con_interes_e_inflacion_variable():
    inflaciones = [Decimal("3.0"), Decimal("2.5"), Decimal("2.0")]
    plan = calcular_plan_cuotas(
        precio=Decimal("30000"),
        cantidad=3,
        tna=Decimal("24"),
        inflacion_mensual=inflaciones,
    )
    assert plan.monto_cuota > Decimal("10000.00")
    assert plan.valor_presente is not None
    assert plan.valor_presente < plan.total


def test_plan_cuotas_cantidad_invalida():
    with pytest.raises(ValueError, match="mayor a 0"):
        calcular_plan_cuotas(Decimal("10000"), 0)
