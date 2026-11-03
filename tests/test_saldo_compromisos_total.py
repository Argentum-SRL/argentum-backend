"""
tests/test_saldo_compromisos_total.py — Pruebas unitarias para total_compromisos en SaldoDisponibleMoneda.
"""
from decimal import Decimal
from app.schemas.dashboard import SaldoDisponibleMoneda


def test_saldo_disponible_moneda_total_compromisos():
    item = SaldoDisponibleMoneda(
        saldo_total=Decimal("500000.00"),
        cuotas_pendientes=Decimal("10000.00"),
        suscripciones_pendientes=Decimal("15000.00"),
        facturas_pendientes=Decimal("5000.00"),
        saldo_disponible=Decimal("470000.00"),
        compromisos=[],
    )
    assert item.total_compromisos == Decimal("30000.00")
    dump = item.model_dump(mode="json")
    assert "total_compromisos" in dump
    assert Decimal(str(dump["total_compromisos"])) == Decimal("30000.00")

    item_deficit = SaldoDisponibleMoneda(
        saldo_total=Decimal("10000.00"),
        cuotas_pendientes=Decimal("0.00"),
        suscripciones_pendientes=Decimal("0.00"),
        facturas_pendientes=Decimal("0.00"),
        saldo_disponible=Decimal("-20000.00"),
        compromisos=[],
    )
    assert item_deficit.total_compromisos == Decimal("30000.00")

