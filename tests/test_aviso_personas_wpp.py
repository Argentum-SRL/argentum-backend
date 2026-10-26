# -*- coding: utf-8 -*-
"""
tests/test_aviso_personas_wpp.py - Tests T.5 para el aviso de Pagos/Cobros de personas en WhatsApp.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from app.routers.whatsapp.aviso_personas_wpp import (
    TEXTO_AVISO_PAGOS,
    TEXTO_AVISO_COBROS,
    agregar_aviso_personas,
)
from app.services.whatsapp_service import BuzonSalida, _buzon_actual_cv


def test_t5_textos_exactos():
    assert TEXTO_AVISO_PAGOS == "Si te acordás para qué era, decime «era el súper» y lo cambio."
    assert TEXTO_AVISO_COBROS == "Si te acordás qué era, decime «era un regalo» y lo cambio."

    assert len(TEXTO_AVISO_PAGOS) <= 90
    assert len(TEXTO_AVISO_COBROS) <= 90
    assert "\n" not in TEXTO_AVISO_PAGOS
    assert "\n" not in TEXTO_AVISO_COBROS


def _crear_contexto_buzon(subcategorias_registradas: list[str], aviso_previo: str | None = None):
    usuario = MagicMock()
    usuario.id = uuid4()
    buzon = BuzonSalida("5491112345678")
    if aviso_previo:
        buzon.agregar_aviso(aviso_previo, "recordatorio_impago")

    token = _buzon_actual_cv.set(buzon)

    db_mock = MagicMock()
    exec_res = MagicMock()
    exec_res.scalars.return_value.all.return_value = subcategorias_registradas
    db_mock.execute.return_value = exec_res

    return usuario, buzon, db_mock, token


def test_t5_movimiento_pagos_a_personas():
    usuario, buzon, db_mock, token = _crear_contexto_buzon(["Pagos a personas"])
    try:
        dt_inicio = datetime.now(timezone.utc)
        res = agregar_aviso_personas(db_mock, usuario, dt_inicio)
        assert res is True
        assert buzon._aviso_texto == TEXTO_AVISO_PAGOS
    finally:
        _buzon_actual_cv.reset(token)


def test_t5_movimiento_cobros_de_personas():
    usuario, buzon, db_mock, token = _crear_contexto_buzon(["Cobros de personas"])
    try:
        dt_inicio = datetime.now(timezone.utc)
        res = agregar_aviso_personas(db_mock, usuario, dt_inicio)
        assert res is True
        assert buzon._aviso_texto == TEXTO_AVISO_COBROS
    finally:
        _buzon_actual_cv.reset(token)


def test_t5_ambos_movimientos():
    usuario, buzon, db_mock, token = _crear_contexto_buzon(["Pagos a personas", "Cobros de personas"])
    try:
        dt_inicio = datetime.now(timezone.utc)
        res = agregar_aviso_personas(db_mock, usuario, dt_inicio)
        assert res is True
        assert buzon._aviso_texto == TEXTO_AVISO_PAGOS
    finally:
        _buzon_actual_cv.reset(token)


def test_t5_sin_ninguno():
    usuario, buzon, db_mock, token = _crear_contexto_buzon(["Supermercado", "Restaurantes"])
    try:
        dt_inicio = datetime.now(timezone.utc)
        res = agregar_aviso_personas(db_mock, usuario, dt_inicio)
        assert res is False
        assert buzon._aviso_texto is None
    finally:
        _buzon_actual_cv.reset(token)


def test_t5_prioridad_recordatorio_impago():
    recordatorio_texto = "Tenés el resumen de Galicia por vencer."
    usuario, buzon, db_mock, token = _crear_contexto_buzon(["Pagos a personas"], aviso_previo=recordatorio_texto)
    try:
        dt_inicio = datetime.now(timezone.utc)
        agregar_aviso_personas(db_mock, usuario, dt_inicio)
        # Buzon ya tenía aviso, por ende se conserva el recordatorio
        assert buzon._aviso_texto == recordatorio_texto
    finally:
        _buzon_actual_cv.reset(token)
