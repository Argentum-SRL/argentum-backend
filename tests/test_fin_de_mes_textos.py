"""
tests/test_fin_de_mes_textos.py — Pruebas unitarias para textos web y mensaje de WhatsApp de fin de mes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.contrato_numero_service import ContratoNumero
from app.services.fin_de_mes_service import ResultadoFinDeMes, ResultadoRango
from app.services.fin_de_mes_textos import (
    mensaje_wpp,
    signo,
    textos_tarjeta,
)
from app.services.frescura_service import Frescura


def _crear_resultado(
    libre_ars: Decimal = Decimal("445000"),
    libre_usd: Decimal = Decimal("0"),
    libre_hasta_cobro: Decimal | None = Decimal("485000"),
    ingreso_esperado: Decimal = Decimal("1000000"),
    fecha_limite: date = date(2026, 11, 24),
    hasta_ingreso: bool = True,
    proximo_ingreso: date | None = date(2026, 11, 25),
    cobra_en_ciclo: bool = True,
    estado: str = "completo",
    rango_bajo: Decimal | None = Decimal("1145000"),
    rango_alto: Decimal | None = Decimal("1345000"),
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
        base_hasta=date(2026, 11, 30),
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
        cobra_en_ciclo=cobra_en_ciclo,
        estado=estado,
        libre_ars=libre_ars,
        libre_hasta_cobro=libre_hasta_cobro,
        ingreso_esperado=ingreso_esperado,
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
        "Llegarías al 30/11 con entre $1.145.000 y $1.345.000.\n"
        "Hasta tu cobro del 25/11 te quedan $485.000 libres."
    )
    assert mensaje_wpp(res) == esperado


def test_mensaje_wpp_caso_sin_cobro_en_ciclo():
    res = _crear_resultado(
        cobra_en_ciclo=False,
        proximo_ingreso=date(2026, 12, 5),
        libre_hasta_cobro=None,
        rango_bajo=Decimal("-100000"),
        rango_alto=Decimal("100000"),
        veces_no_alcanzo=1,
        ciclos=3,
        factor_cobertura=Decimal("1.25"),
    )
    esperado = (
        "Llegarías al 30/11 con entre -$100.000 y $100.000.\n"
        "En 1 de tus últimos 3 meses no te alcanzó.\n"
        "Puede que no hayas cargado todo: el rango ya lo tiene en cuenta."
    )
    assert mensaje_wpp(res) == esperado


def test_mensaje_wpp_rango_unico_y_textos_tarjeta():
    res = _crear_resultado(
        rango_bajo=Decimal("40000"),
        rango_alto=Decimal("40000"),
    )
    msg = mensaje_wpp(res)
    primera_linea = msg.split("\n")[0]
    assert primera_linea == "Llegarías al 30/11 con unos $40.000."

    tt = textos_tarjeta(res)
    assert tt is not None
    assert tt["monto"] == "Unos $40.000"


def test_mensaje_wpp_caso_sin_ingreso():
    res = _crear_resultado(
        estado="sin_ingreso",
        libre_ars=Decimal("445000"),
        cobra_en_ciclo=False,
        proximo_ingreso=None,
        libre_hasta_cobro=None,
        rango_bajo=None,
        rango_alto=None,
        veces_no_alcanzo=None,
    )
    esperado = (
        "Todavía no tengo suficientes meses tuyos para decirte con cuánto llegás.\n"
        "Hoy tenés $445.000 libres."
    )
    assert mensaje_wpp(res) == esperado


def test_mensaje_wpp_caso_solo_seguro():
    res = _crear_resultado(
        estado="solo_seguro",
        libre_ars=Decimal("-20000"),
        cobra_en_ciclo=False,
        proximo_ingreso=None,
        libre_hasta_cobro=None,
        rango_bajo=None,
        rango_alto=None,
        veces_no_alcanzo=None,
    )
    esperado = (
        "Todavía no tengo suficientes meses tuyos para decirte con cuánto llegás.\n"
        "Hoy te faltan $20.000 para cubrir lo que vence."
    )
    assert mensaje_wpp(res) == esperado


def test_mensaje_wpp_datos_viejos():
    res = _crear_resultado(
        estado="datos_viejos",
        datos_viejos=True,
        dias_sin_cargar=13,
        libre_ars=Decimal("445000"),
        cobra_en_ciclo=False,
        proximo_ingreso=None,
        libre_hasta_cobro=None,
        rango_bajo=None,
        rango_alto=None,
        veces_no_alcanzo=None,
    )
    msg = mensaje_wpp(res)
    lineas = msg.split("\n")
    assert len(lineas) >= 3
    assert lineas[2] == "Ojo: hace 13 días que no cargás nada, puede no estar al día."


def test_mensaje_wpp_con_dolares():
    res = _crear_resultado(
        libre_usd=Decimal("150"),
    )
    msg = mensaje_wpp(res)
    lineas = msg.split("\n")
    assert lineas[-1] == "En dólares: US$150 libres."


def test_mensaje_wpp_limite_caracteres_90():
    res = _crear_resultado(
        ciclos=12,
        veces_no_alcanzo=12,
        rango_bajo=Decimal("-999999999"),
        rango_alto=Decimal("-999999999"),
        libre_hasta_cobro=Decimal("-999999999"),
        cobra_en_ciclo=True,
        factor_cobertura=Decimal("2"),
        libre_usd=Decimal("999999"),
    )
    msg = mensaje_wpp(res)
    for linea in msg.split("\n"):
        assert len(linea) <= 90, f"Línea excede 90 caracteres: {linea}"


def test_textos_tarjeta_sin_cobro_en_ciclo():
    res = _crear_resultado(
        cobra_en_ciclo=False,
        proximo_ingreso=date(2026, 12, 5),
        libre_hasta_cobro=None,
        rango_bajo=Decimal("-100000"),
        rango_alto=Decimal("100000"),
        veces_no_alcanzo=1,
        ciclos=3,
        factor_cobertura=Decimal("1.25"),
    )
    tt = textos_tarjeta(res)
    assert tt is not None
    assert tt["subtitulo"] == "Es con lo que llegarías al 30/11, cuando termina tu ciclo."
    assert tt["linea_cobro"] is None
    assert tt["linea_riesgo"] == "En 1 de tus últimos 3 meses no te alcanzó."


def test_textos_tarjeta_solo_seguro():
    res = _crear_resultado(
        estado="solo_seguro",
        libre_ars=Decimal("-20000"),
        cobra_en_ciclo=False,
        proximo_ingreso=None,
        libre_hasta_cobro=None,
        rango_bajo=None,
        rango_alto=None,
        veces_no_alcanzo=None,
    )
    assert textos_tarjeta(res) is None


def test_signo():
    assert signo(Decimal("50000")) == "+$50.000"
    assert signo(Decimal("-50000")) == "-$50.000"
    assert signo(Decimal("0")) == "$0"
