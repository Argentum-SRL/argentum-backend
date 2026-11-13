# -*- coding: utf-8 -*-
"""
tests/test_mezcla_montos.py — fase_arreglo_mezcla (12/11/2026).

Caso real de producción: "pase los 20.000 de mercado pago a la cuenta de banco macro"
contestaba "deben registrarse en mensajes separados de los gastos o ingresos", porque
"20.000" se contaba como dos montos. Un número con separador de miles o decimales es
un solo monto y las fechas no son montos.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.routers.whatsapp.detectors import _bloquear_mezcla_en_handlers, _numeros_de_monto


@pytest.mark.parametrize(
    "texto, esperado",
    [
        ("pase 20.000 de galicia a macro", ["20.000"]),
        ("pase 1,5 palos de galicia a macro", ["1,5"]),
        ("pase 1.500.000,50 de galicia a macro", ["1.500.000,50"]),
        ("pase 20k de galicia a macro el 12/11", ["20"]),
        ("pase 20.000 de galicia a macro el 12/11/2026", ["20.000"]),
        ("pase 20.000 de galicia a macro el 12 de noviembre", ["20.000"]),
        ("gaste 5000 en el kiosco y pase 20000 de galicia a macro", ["5000", "20000"]),
        ("pague 3.000 de luz y pase 2.000 a mp", ["3.000", "2.000"]),
    ],
)
def test_numeros_de_monto(texto, esperado):
    assert _numeros_de_monto(texto) == esperado


def _llamar(texto: str):
    usuario = MagicMock()
    db = MagicMock()
    with patch("app.routers.whatsapp.detectors._debe_bloquear_mezcla_lote", return_value=True), \
         patch("app.services.whatsapp_service.enviar_whatsapp") as envio:
        res = _bloquear_mezcla_en_handlers(texto, usuario, db, "5490000000000", "wamid_test")
    return res, envio


def test_transferencia_con_punto_de_miles_no_se_bloquea():
    res, envio = _llamar("pase los 20.000 de mercado pago a la cuenta de banco macro")
    assert res is False
    envio.assert_not_called()


def test_transferencia_con_decimales_y_fecha_no_se_bloquea():
    res, envio = _llamar("pase 1,5 palos de galicia a macro el 12/11")
    assert res is False
    envio.assert_not_called()


def test_mezcla_real_sigue_bloqueada():
    from app.routers.whatsapp_ia import MSG_NO_MEZCLAR_TRANSFERENCIAS
    res, envio = _llamar("gasté 5000 en el kiosco y pasé 20000 de galicia a macro")
    assert res is True
    envio.assert_called_once()
    assert envio.call_args[0][1] == MSG_NO_MEZCLAR_TRANSFERENCIAS


def test_dos_montos_con_punto_siguen_bloqueados():
    res, envio = _llamar("pase 20.000 de galicia a macro y 5.000 de galicia a mp")
    assert res is True
    envio.assert_called_once()
