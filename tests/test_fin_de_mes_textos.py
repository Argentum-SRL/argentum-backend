"""
tests/test_fin_de_mes_textos.py — Pruebas unitarias para textos web y mensaje de WhatsApp de fin de mes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.contrato_numero_service import ContratoNumero
from app.services.fin_de_mes_service import ResultadoFinDeMes, ResultadoRango
from app.services.fin_de_mes_textos import (
    calendario,
    linea_cobertura,
    linea_rango,
    mensaje_wpp,
    signo,
)
from app.services.frescura_service import Frescura


def _crear_resultado(
    libre_ars: Decimal = Decimal("485000"),
    libre_usd: Decimal = Decimal("0"),
    fecha_limite: date = date(2026, 11, 24),
    hasta_ingreso: bool = True,
    proximo_ingreso: date | None = date(2026, 11, 25),
    estado: str = "completo",
    rango_bajo: Decimal | None = Decimal("365000"),
    rango_alto: Decimal | None = Decimal("445000"),
    ciclos: int = 3,
    veces_no_alcanzo: int | None = 0,
    factor_cobertura: Decimal = Decimal("1"),
    saldo_hoy: Decimal = Decimal("500000"),
    saldo_inicio: Decimal = Decimal("550000"),
    movimiento: Decimal = Decimal("-50000"),
    compromisos_ars: list | None = None,
    compromisos_usd: list | None = None,
    datos_viejos: bool = False,
    dias_sin_cargar: int | None = None,
) -> ResultadoFinDeMes:
    rango = ResultadoRango(
        estado=estado,
        rango_bajo=rango_bajo,
        rango_alto=rango_alto,
        ciclos=ciclos,
        veces_no_alcanza=veces_no_alcanzo,
        factor_cobertura=factor_cobertura,
    )
    frescura = Frescura(
        ultima_carga=date(2026, 11, 19) if not datos_viejos else date(2026, 11, 1),
        dias_sin_cargar=dias_sin_cargar or (1 if not datos_viejos else 19),
        umbral_dias=7,
        intervalos=5,
        datos_viejos=datos_viejos,
    )
    contrato = ContratoNumero(
        valor=libre_ars,
        moneda="ARS",
        base_desde=date(2026, 11, 1),
        base_hasta=fecha_limite,
        fecha_corte=date(2026, 11, 20),
        cobertura=None,
        frescura=frescura,
        supuestos=(),
    )
    return ResultadoFinDeMes(
        hoy=date(2026, 11, 20),
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        limite=fecha_limite,
        proximo_ingreso=proximo_ingreso,
        hasta_ingreso=hasta_ingreso,
        estado=estado,
        libre_ars=libre_ars,
        libre_usd=libre_usd,
        compromisos_ars=compromisos_ars or [],
        compromisos_usd=compromisos_usd or [],
        rango=rango,
        cobertura=None,
        saldo_inicio=saldo_inicio,
        movimiento=movimiento,
        saldo_hoy=saldo_hoy,
        frescura=frescura,
        contrato=contrato,
    )


def test_mensaje_wpp_caso_completo():
    res = _crear_resultado()
    esperado = (
        "Te quedan $485.000 libres hasta el 24/11, antes de tu próximo cobro.\n"
        "A esa fecha llegarías con entre $365.000 y $445.000."
    )
    assert mensaje_wpp(res) == esperado


def test_mensaje_wpp_caso_sin_ingreso():
    res = _crear_resultado(
        libre_ars=Decimal("445000"),
        proximo_ingreso=None,
        fecha_limite=date(2026, 11, 30),
        hasta_ingreso=False,
        estado="sin_ingreso",
        rango_bajo=None,
        rango_alto=None,
        veces_no_alcanzo=None,
    )
    esperado = (
        "Te quedan $445.000 libres hasta el 30/11, fin de tu ciclo.\n"
        "Cuando cargues tus ingresos vas a ver cómo llegás a esa fecha."
    )
    assert mensaje_wpp(res) == esperado


def test_mensaje_wpp_datos_viejos():
    res = _crear_resultado(
        datos_viejos=True,
        dias_sin_cargar=13,
        estado="datos_viejos",
        rango_bajo=None,
        rango_alto=None,
        veces_no_alcanzo=None,
    )
    msg = mensaje_wpp(res)
    lineas = msg.split("\n")
    assert len(lineas) >= 2
    assert lineas[1] == "Ojo: hace 13 días que no cargás nada, puede no estar al día."


def test_mensaje_wpp_libre_negativo():
    res = _crear_resultado(
        libre_ars=Decimal("-20000"),
        proximo_ingreso=None,
        fecha_limite=date(2026, 11, 30),
        hasta_ingreso=False,
        estado="sin_ingreso",
        rango_bajo=None,
        rango_alto=None,
        veces_no_alcanzo=None,
    )
    msg = mensaje_wpp(res)
    lineas = msg.split("\n")
    assert lineas[0] == "Hasta el 30/11 te faltan $20.000 para cubrir lo que vence."


def test_mensaje_wpp_riesgo_veces():
    res = _crear_resultado(
        ciclos=8,
        veces_no_alcanzo=2,
    )
    msg = mensaje_wpp(res)
    lineas = msg.split("\n")
    assert len(lineas) == 3
    assert lineas[2] == "Gastando como en tus últimos 8 meses, en 2 no te alcanzaría."


def test_mensaje_wpp_dolares():
    res = _crear_resultado(
        libre_usd=Decimal("150"),
    )
    msg = mensaje_wpp(res)
    lineas = msg.split("\n")
    assert lineas[-1] == "En dólares: US$150 libres."


def test_mensaje_wpp_limite_caracteres_90():
    res = _crear_resultado(
        libre_ars=Decimal("-999999999"),
        libre_usd=Decimal("999999"),
        ciclos=12,
        veces_no_alcanzo=12,
        datos_viejos=True,
        dias_sin_cargar=99,
        rango_bajo=Decimal("-1234567890"),
        rango_alto=Decimal("1234567890"),
    )
    msg = mensaje_wpp(res)
    for linea in msg.split("\n"):
        assert len(linea) <= 90, f"Línea excede 90 caracteres: {linea}"


def test_linea_rango_unico_valor():
    assert linea_rango(Decimal("40000"), Decimal("40000")) == "A esa fecha llegarías con unos $40.000."


def test_linea_cobertura():
    assert linea_cobertura(Decimal("1.25")) == "Puede que no hayas cargado todo: el rango ya lo tiene en cuenta."


def test_signo():
    assert signo(Decimal("50000")) == "+$50.000"
    assert signo(Decimal("-50000")) == "-$50.000"
    assert signo(Decimal("0")) == "$0"


def test_calendario():
    items = [{"fecha_cobro": "2026-11-22", "nombre": "Gimnasio", "monto": Decimal("15000")}]
    cal = calendario(items, "ARS")
    assert cal == [{"fecha": "22/11", "nombre": "Gimnasio", "monto": "$15.000"}]
