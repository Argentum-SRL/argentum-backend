# -*- coding: utf-8 -*-
"""
tests/test_permitirse_textos_t1.py — Pruebas de textos nuevos de T1 (TX1 a TX13).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.permitirse_textos import armar_respuesta_permitirse, textos_permitirse
from app.services.tools_service import PlanCuotas


def _todas_lineas_wpp(casos: list[tuple[dict, dict]]) -> list[str]:
    lineas: list[str] = []
    for res, cons in casos:
        txt = armar_respuesta_permitirse(res, cons)
        lineas.extend(txt.split("\n"))
    return lineas


def test_tx1_cuotas_justo_con_cuentas():
    res = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "usa_cuentas": True,
        "uso_mensual": Decimal("100000"),
        "cantidad_cuotas": 6,
    })
    assert (
        res["linea_principal"]
        == "De cada cuota, unos $100.000 salen de la plata que ya tenés en tus cuentas."
    )
    assert res["linea_cuentas"] == "Te alcanza para las 6 cuotas."


def test_tx2_y_tx2b_cuotas_ahorros():
    res_60 = textos_permitirse({
        "modo": "cuotas",
        "resultado": "ahorros",
        "falta": Decimal("60000"),
        "cantidad_cuotas": 6,
    })
    assert res_60["linea_principal"] == (
        "Usando la plata de tus cuentas te faltarían $60.000: "
        "tendrías que sacarlos de tus ahorros."
    )

    res_160 = textos_permitirse({
        "modo": "cuotas",
        "resultado": "ahorros",
        "falta": Decimal("160000"),
        "cantidad_cuotas": 6,
    })
    esperada_web = (
        "Usando la plata de tus cuentas te faltarían $160.000: "
        "tendrías que sacarlos de tus ahorros."
    )
    assert res_160["linea_principal"] == esperada_web

    wpp = armar_respuesta_permitirse(res_160, {"modo": "cuotas", "cantidad_cuotas": 6})
    lineas = wpp.split("\n")
    assert "Usando la plata de tus cuentas te faltarían $160.000:" in lineas
    assert "tendrías que sacarlos de tus ahorros." in lineas


def test_tx3_cuotas_no_alcanza():
    res0 = textos_permitirse({
        "modo": "cuotas",
        "resultado": "no_alcanza",
        "falta": Decimal("500000"),
        "ahorros_total": Decimal("0"),
    })
    assert (
        res0["linea_principal"]
        == "Te faltarían $500.000 aun usando la plata de tus cuentas."
    )

    res10 = textos_permitirse({
        "modo": "cuotas",
        "resultado": "no_alcanza",
        "falta": Decimal("500000"),
        "ahorros_total": Decimal("10000"),
    })
    assert (
        res10["linea_principal"]
        == "Te faltarían $500.000 aun usando la plata de tus cuentas y tus ahorros."
    )


def test_tx4_mensual_justo():
    r20 = textos_permitirse({
        "modo": "mensual",
        "resultado": "justo",
        "usa_cuentas": True,
        "meses_cuentas": 20,
    })
    assert r20["linea_principal"] == "Con la plata de tus cuentas lo bancás 20 meses."

    r120 = textos_permitirse({
        "modo": "mensual",
        "resultado": "justo",
        "usa_cuentas": True,
        "meses_cuentas": 120,
    })
    assert r120["linea_principal"] == "Con la plata de tus cuentas lo bancás más de 2 años."


def test_tx5_mensual_ahorros():
    r6 = textos_permitirse({
        "modo": "mensual",
        "resultado": "ahorros",
        "meses_cuentas": 6,
    })
    assert (
        r6["linea_principal"]
        == "Con la plata de tus cuentas lo bancás 6 meses; después tendrías que usar tus ahorros."
    )

    r1 = textos_permitirse({
        "modo": "mensual",
        "resultado": "ahorros",
        "meses_cuentas": 1,
    })
    assert (
        r1["linea_principal"]
        == "Con la plata de tus cuentas lo bancás 1 mes; después tendrías que usar tus ahorros."
    )


def test_tx6_mensual_no_alcanza():
    r0 = textos_permitirse({
        "modo": "mensual",
        "resultado": "no_alcanza",
        "meses_cuentas": 0,
        "meses_total": 0,
        "ahorros_total": Decimal("0"),
    })
    assert r0["linea_principal"] == "No te alcanza ni para un mes."

    r3 = textos_permitirse({
        "modo": "mensual",
        "resultado": "no_alcanza",
        "meses_cuentas": 0,
        "meses_total": 3,
        "ahorros_total": Decimal("100000"),
    })
    assert (
        r3["linea_principal"]
        == "Con la plata de tus cuentas y tus ahorros lo bancás 3 meses."
    )


def test_tx7_contado_en_rojo():
    res = textos_permitirse({
        "modo": "contado",
        "resultado": "no_alcanza",
        "libre": Decimal("-460517"),
        "precio": Decimal("50000"),
        "falta": Decimal("510517"),
        "fin_ciclo": date(2026, 11, 30),
    })
    assert (
        res["linea_principal"]
        == "Este mes ya te faltan $460.517 para tus pagos; con esta compra, $510.517."
    )


def test_tx8_juntar():
    r2 = textos_permitirse({
        "modo": "contado",
        "resultado": "ahorros",
        "falta_bruta": Decimal("200000"),
        "sobrante": Decimal("100000"),
    })
    assert r2["linea_juntar"] == "Con lo que te sobra por mes, lo juntás en 2 meses."

    r1 = textos_permitirse({
        "modo": "contado",
        "resultado": "ahorros",
        "falta_bruta": Decimal("90000"),
        "sobrante": Decimal("100000"),
    })
    assert r1["linea_juntar"] == "Con lo que te sobra por mes, lo juntás en 1 mes."

    r40 = textos_permitirse({
        "modo": "contado",
        "resultado": "ahorros",
        "falta_bruta": Decimal("4000000"),
        "sobrante": Decimal("100000"),
    })
    assert r40["linea_juntar"] is None


def test_tx9_compensar_recortable():
    rc = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "rubro_compensar": "Delivery",
        "monto_compensar": Decimal("20000"),
    })
    assert rc["linea_compensar"] == "Si gastás $20.000 menos por mes en Delivery, te entra bien."

    rk = textos_permitirse({
        "modo": "contado",
        "resultado": "justo",
        "rubro_compensar": "Delivery",
        "monto_compensar": Decimal("20000"),
    })
    assert (
        rk["linea_compensar"]
        == "Si este mes gastás $20.000 menos en Delivery, te entra bien."
    )


def test_tx10_reintegro():
    ra = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "rubro_compensar": "Delivery",
        "monto_compensar": Decimal("20000"),
        "reintegro": (Decimal("8000"), "Visa Galicia"),
    })
    assert (
        ra["linea_reintegro"]
        == "En Delivery ya te devolvieron $8.000 con Visa Galicia: pagá con esa."
    )

    rb = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "rubro_compensar": "Delivery",
        "monto_compensar": Decimal("20000"),
        "reintegro": None,
    })
    assert (
        rb["linea_reintegro"]
        == "Fijate si tu banco o billetera tiene reintegros en Delivery."
    )


def test_tx11_precio_en_dolares():
    res = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "moneda": "USD",
        "cotizacion": Decimal("1450"),
        "usa_cuentas": True,
        "uso_mensual": Decimal("145000"),
        "cantidad_cuotas": 6,
    })
    assert res["linea_cotizacion"] == "Lo calculé con el dólar oficial de hoy ($1.450)."
    assert (
        res["linea_principal"]
        == "De cada cuota, unos US$100 salen de la plata que ya tenés en tus cuentas."
    )


def test_tx12_fuente_inflacion():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.expectativa_inflacion import ExpectativaInflacion
    from app.services.inflacion_esperada_service import inflacion_esperada

    eng = create_engine("sqlite:///:memory:")
    ExpectativaInflacion.__table__.create(bind=eng)
    TestSes = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    with TestSes() as ses:
        for mes_num in range(12):
            anio = 2026 + (11 + mes_num) // 12
            mes = ((11 + mes_num) % 12) + 1
            ses.add(ExpectativaInflacion(
                relevamiento="2026-11",
                periodo=f"{anio:04d}-{mes:02d}",
                valor=Decimal("2.50"),
                fuente="REM BCRA",
            ))
        ses.commit()
        ie = inflacion_esperada(ses, hoy=date(2026, 11, 17), meses=6)

    assert ie is not None
    assert ie.fuente_texto == "la inflación que se espera"
    assert "REM" not in ie.fuente_texto

    plan = PlanCuotas(
        monto_cuota=Decimal("100000"),
        total=Decimal("600000"),
        interes=Decimal("0"),
        valor_presente=Decimal("528767.06"),
    )
    res = textos_permitirse({
        "modo": "cuotas",
        "resultado": "justo",
        "cantidad_cuotas": 6,
        "plan": plan,
        "fuente_inflacion": ie.fuente_texto,
    })
    assert (
        res["lineas_plan"][1]
        == "Con la inflación que se espera, en plata de hoy equivalen a $528.767."
    )


def test_tx13_whatsapp_largo_y_sin_tecnicismos():
    plan = PlanCuotas(
        monto_cuota=Decimal("100000"),
        total=Decimal("600000"),
        interes=Decimal("0"),
        valor_presente=Decimal("528767.06"),
    )
    casos = [
        (
            textos_permitirse({
                "modo": "cuotas",
                "resultado": "justo",
                "usa_cuentas": True,
                "uso_mensual": Decimal("100000"),
                "cantidad_cuotas": 6,
                "plan": plan,
                "fuente_inflacion": "la inflación que se espera",
            }),
            {"modo": "cuotas", "cantidad_cuotas": 6},
        ),
        (
            textos_permitirse({
                "modo": "cuotas",
                "resultado": "ahorros",
                "falta": Decimal("160000"),
                "ahorros_total": Decimal("300000"),
                "cantidad_cuotas": 6,
                "rubro_compensar": "Delivery",
                "monto_compensar": Decimal("20000"),
                "reintegro": (Decimal("8000"), "Visa Galicia"),
            }),
            {"modo": "cuotas", "cantidad_cuotas": 6},
        ),
        (
            textos_permitirse({
                "modo": "cuotas",
                "resultado": "no_alcanza",
                "falta": Decimal("500000"),
                "ahorros_total": Decimal("10000"),
                "cantidad_cuotas": 6,
            }),
            {"modo": "cuotas", "cantidad_cuotas": 6},
        ),
        (
            textos_permitirse({
                "modo": "mensual",
                "resultado": "justo",
                "usa_cuentas": True,
                "meses_cuentas": 120,
            }),
            {"modo": "mensual", "cantidad_cuotas": 1},
        ),
        (
            textos_permitirse({
                "modo": "mensual",
                "resultado": "ahorros",
                "meses_cuentas": 6,
                "ahorros_total": Decimal("200000"),
            }),
            {"modo": "mensual", "cantidad_cuotas": 1},
        ),
        (
            textos_permitirse({
                "modo": "mensual",
                "resultado": "no_alcanza",
                "meses_cuentas": 0,
                "meses_total": 3,
                "ahorros_total": Decimal("100000"),
            }),
            {"modo": "mensual", "cantidad_cuotas": 1},
        ),
        (
            textos_permitirse({
                "modo": "contado",
                "resultado": "no_alcanza",
                "libre": Decimal("-460517"),
                "precio": Decimal("50000"),
                "falta": Decimal("510517"),
                "falta_bruta": Decimal("510517"),
                "sobrante": Decimal("100000"),
                "fin_ciclo": date(2026, 11, 30),
            }),
            {"modo": "contado", "cantidad_cuotas": 1},
        ),
        (
            textos_permitirse({
                "modo": "cuotas",
                "resultado": "justo",
                "moneda": "USD",
                "cotizacion": Decimal("1450"),
                "usa_cuentas": True,
                "uso_mensual": Decimal("145000"),
                "cantidad_cuotas": 6,
            }),
            {"modo": "cuotas", "cantidad_cuotas": 6},
        ),
    ]
    for linea in _todas_lineas_wpp(casos):
        assert len(linea) <= 90, f"Línea supera 90 chars ({len(linea)}): {linea}"
        for prohibido in ("REM", "TNA", "ARS", "USD"):
            assert prohibido not in linea, f"Contiene {prohibido}: {linea}"
