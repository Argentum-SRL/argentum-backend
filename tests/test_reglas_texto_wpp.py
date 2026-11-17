"""Tests puros de reglas, formato corto y control de textos de WhatsApp."""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import random
import subprocess
import sys
from types import SimpleNamespace

from app.core.reglas_texto_wpp import (
    ejemplo_wpp,
    fecha_corta_wpp,
    nombre_tarjeta_wpp,
    partir_lineas_wpp,
    problemas_texto_wpp,
)
from scripts.regresion.suite.comun import revisar_reglas_textos_suite


def test_tr1_problemas_texto_wpp():
    assert problemas_texto_wpp("Listo, anoté $5.000 en Kiosco desde Galicia.") == []
    assert problemas_texto_wpp(
        "Listo. $5.000 en Kiosco desde Galicia — registrado.\n"
        "Si fue con otra, decime cuál."
    ) == ["R2_PALABRA:registrar", "R6_RAYA"]
    assert problemas_texto_wpp(
        "¿Querés eliminar el último movimiento? ¿Confirmás?"
    ) == ["R2_PALABRA:eliminar", "R7_MAS_DE_UNA_PREGUNTA"]

    assert problemas_texto_wpp("a" * 91) == ["R1_LINEA_LARGA:1:91"]
    assert problemas_texto_wpp("a" * 90) == []

    assert problemas_texto_wpp(
        "¿Desde qué billetera salió la plata?\n\n1. Galicia"
    ) == ["R2_PALABRA:billetera", "R10_LISTA"]
    assert problemas_texto_wpp("Hola, «Kuma»") == ["R3_COMILLAS"]
    assert problemas_texto_wpp("Probá así: _gasté 5000 en el kiosco_") == []
    assert problemas_texto_wpp("*Galicia*: $100") == ["R4_NEGRITA"]
    assert problemas_texto_wpp("Tenés 1 movimiento(s)") == ["R5_PLURAL_PARENTESIS"]
    assert (
        problemas_texto_wpp(
            "Decime cuánto sale. Por ejemplo: _¿me puedo permitir algo de 300.000 en 6 cuotas?_"
        )
        == []
    )
    assert problemas_texto_wpp(
        "¿Lo anoto?\nSi fue con otra, decime cuál."
    ) == ["R7_PREGUNTA_NO_AL_FINAL"]
    assert (
        problemas_texto_wpp(
            "Factura de EPE: $1.234,50. ¿Ya la pagaste?\nSí: la anoto.\nNo: la guardo."
        )
        == []
    )
    assert problemas_texto_wpp("Ojo: uno.\nOjo: dos.") == ["R8_OJO_REPETIDO"]
    assert problemas_texto_wpp("Tenés $4.370.800,87 en tus cuentas.") == ["R9_CENTAVOS"]
    assert problemas_texto_wpp("12 cuotas de $6.666,67") == []
    assert problemas_texto_wpp("• Galicia") == ["R10_LISTA"]
    assert problemas_texto_wpp("1) Galicia") == ["R10_LISTA"]
    assert problemas_texto_wpp("Listo, anoté None") == ["R11_SIN_RELLENAR"]
    assert problemas_texto_wpp("vence el 18/10/2026") == ["R12_FECHA"]
    assert problemas_texto_wpp("el 9 de octubre") == ["R12_FECHA"]
    assert problemas_texto_wpp("anteayer") == ["R12_FECHA"]
    assert problemas_texto_wpp("Listo 👍") == ["R13_EMOJI"]
    assert problemas_texto_wpp("Podés gestionarlo en la web") == [
        "R2_PALABRA:gestionar",
        "R2_PALABRA:la web",
    ]
    assert problemas_texto_wpp("Si tienes dudas") == ["R2_PALABRA:tuteo"]
    assert problemas_texto_wpp("La TNA es 60%") == ["R2_PALABRA:TNA"]
    assert problemas_texto_wpp("la tna") == []
    assert (
        problemas_texto_wpp(
            "¿Qué era? 1) Me pagaron 2) Me devolvieron parte de un gasto "
            "3) Solo pasó por mi cuenta"
        )
        == []
    )


def test_tr2_partir_lineas_wpp():
    original = (
        "Usando la plata de tus cuentas te faltarían $160.000: "
        "tendrías que sacarlos de tus ahorros."
    )
    esperado = (
        "Usando la plata de tus cuentas te faltarían $160.000:\n"
        "tendrías que sacarlos de tus ahorros."
    )
    assert partir_lineas_wpp(original) == esperado

    de_90 = "x" * 90
    assert partir_lineas_wpp(de_90) == de_90

    sin_punt = " ".join(["palabra"] * 15)  # 119 chars, espacios cada 8 chars
    assert len(sin_punt) > 90
    partido = partir_lineas_wpp(sin_punt)
    lineas_p = partido.split("\n")
    assert len(lineas_p) == 2
    assert len(lineas_p[0]) <= 90
    assert len(lineas_p[1]) <= 90
    assert " ".join(lineas_p) == sin_punt

    rng = random.Random(2026)
    abecedario = "abcdefghijklmnopqrstuvwxyz"
    for _ in range(200):
        n_palabras = rng.randint(10, 40)
        palabras = [
            "".join(rng.choice(abecedario) for _ in range(rng.randint(1, 12)))
            for _ in range(n_palabras)
        ]
        frase = " ".join(palabras)
        res = partir_lineas_wpp(frase)
        for linea in res.split("\n"):
            assert len(linea) <= 90
        assert res.replace("\n", " ") == frase


def test_tr3_fecha_corta_wpp():
    hoy = date(2026, 11, 16)
    assert fecha_corta_wpp(date(2026, 11, 16), hoy) == ""
    assert fecha_corta_wpp(date(2026, 11, 15), hoy) == "ayer"
    assert fecha_corta_wpp(date(2026, 11, 14), hoy) == "14/11"
    assert fecha_corta_wpp(date(2026, 10, 1), hoy) == "1/10"
    assert fecha_corta_wpp(date(2026, 10, 9), hoy) == "9/10"


def test_tr4_nombre_tarjeta_y_ejemplo_wpp():
    t_con_b = SimpleNamespace(
        red="visa",
        billetera=SimpleNamespace(nombre="Galicia"),
        nombre="•••• 1506",
    )
    assert nombre_tarjeta_wpp(t_con_b) == "Visa Galicia •••• 1506"

    t_rep_1 = SimpleNamespace(
        red="visa",
        billetera=SimpleNamespace(nombre="Galicia"),
        nombre="Visa",
    )
    assert nombre_tarjeta_wpp(t_rep_1) == "Visa Galicia"

    t_rep_2 = SimpleNamespace(
        red="visa",
        billetera=SimpleNamespace(nombre="Cuenta Galicia"),
        nombre="Galicia Visa",
    )
    assert nombre_tarjeta_wpp(t_rep_2) == "Visa Cuenta Galicia"

    t_sin_b = SimpleNamespace(
        red="visa",
        billetera=None,
        nombre="•••• 1506",
    )
    assert nombre_tarjeta_wpp(t_sin_b) == "Visa •••• 1506"
    assert ejemplo_wpp("gasté 5000 en el kiosco") == "_gasté 5000 en el kiosco_"


def test_tr5_plugin_bloquea_texto_malo(tmp_path: Path):
    archivo = tmp_path / "test_tr5_dummy.py"
    archivo.write_text(
        "from unittest.mock import patch\n"
        "from app.services import whatsapp_service\n\n"
        "def test_manda_raya():\n"
        '    with patch("app.services.whatsapp_service.enviar_whatsapp"):\n'
        '        whatsapp_service.enviar_whatsapp("5491100000000", "Listo — registrado")\n',
        encoding="utf-8",
    )
    raiz = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(raiz)}
    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--noconftest",
            "-p",
            "tests.plugin_reglas_texto",
            "-o",
            "addopts=",
            "-q",
            str(archivo),
        ],
        cwd=str(raiz),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1, res.stdout + res.stderr
    assert "R6_RAYA" in res.stdout + res.stderr


def test_tr6_control_suite_detecta_raya():
    salidas = {
        "P1.1": [{"texto": "Listo — registrado", "lleva_texto_ia": False}],
        "P1.2": [{"texto": "Texto IA — libre", "lleva_texto_ia": True}],
    }
    hallazgos = revisar_reglas_textos_suite(salidas)
    assert len(hallazgos) == 1
    assert hallazgos[0][0] == "P1.1"
    assert "R6_RAYA" in hallazgos[0][2]
