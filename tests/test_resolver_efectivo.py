"""
Pruebas de resolución de billeteras de efectivo por moneda.
"""
import pytest
from uuid import uuid4
from app.models.billetera import Billetera
from app.models.usuario import Moneda
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada


@pytest.fixture
def billeteras_memoria():
    uid = uuid4()
    b_pesos = Billetera(id=uuid4(), usuario_id=uid, nombre="Efectivo Pesos", moneda=Moneda.ARS, es_efectivo=True)
    b_dolares = Billetera(id=uuid4(), usuario_id=uid, nombre="Efectivo Dólares", moneda=Moneda.USD, es_efectivo=True)
    b_galicia = Billetera(id=uuid4(), usuario_id=uid, nombre="Galicia", moneda=Moneda.ARS, es_efectivo=False)
    b_santander = Billetera(id=uuid4(), usuario_id=uid, nombre="Santander", moneda=Moneda.ARS, es_efectivo=False)
    return [b_pesos, b_dolares, b_galicia, b_santander]


def test_resolver_efectivo_dolares(billeteras_memoria):
    b_dolares = billeteras_memoria[1]
    for entrada in ["Efectivo USD", "efectivo dólares", "cash usd"]:
        resuelto, candidatas = resolver_billetera_cascada(entrada, billeteras_memoria)
        assert resuelto == b_dolares, f"Fallo al resolver {entrada}"
        assert candidatas == [b_dolares]


def test_resolver_efectivo_pesos(billeteras_memoria):
    b_pesos = billeteras_memoria[0]
    for entrada in ["Efectivo ARS", "efectivo pesos"]:
        resuelto, candidatas = resolver_billetera_cascada(entrada, billeteras_memoria)
        assert resuelto == b_pesos, f"Fallo al resolver {entrada}"
        assert candidatas == [b_pesos]


def test_resolver_efectivo_ambiguo(billeteras_memoria):
    b_pesos = billeteras_memoria[0]
    b_dolares = billeteras_memoria[1]
    resuelto, candidatas = resolver_billetera_cascada("efectivo", billeteras_memoria)
    assert resuelto is None
    assert set(candidatas) == {b_pesos, b_dolares}


def test_resolver_galicia(billeteras_memoria):
    b_galicia = billeteras_memoria[2]
    resuelto, candidatas = resolver_billetera_cascada("Galicia", billeteras_memoria)
    assert resuelto == b_galicia
    assert candidatas == [b_galicia]


def test_resolver_efectivo_usd_renombrado():
    uid = uuid4()
    b_pesos = Billetera(id=uuid4(), usuario_id=uid, nombre="Efectivo Pesos", moneda=Moneda.ARS, es_efectivo=True)
    b_fisica = Billetera(id=uuid4(), usuario_id=uid, nombre="Billetera física", moneda=Moneda.USD, es_efectivo=True)
    b_galicia = Billetera(id=uuid4(), usuario_id=uid, nombre="Galicia", moneda=Moneda.ARS, es_efectivo=False)
    b_santander = Billetera(id=uuid4(), usuario_id=uid, nombre="Santander", moneda=Moneda.ARS, es_efectivo=False)
    billeteras = [b_pesos, b_fisica, b_galicia, b_santander]

    resuelto, candidatas = resolver_billetera_cascada("efectivo usd", billeteras)
    assert resuelto == b_fisica
    assert candidatas == [b_fisica]
