# -*- coding: utf-8 -*-
"""
Tests unitarios del motor financiero: Detección Temprana de Ingreso Habitual (fase_ingreso_temprano).
Ubicación: tests/motor/test_ingreso_temprano.py
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.ingreso_habitual_service import calcular_ingreso_habitual_en_memoria
from tests.motor.test_ingreso_habitual import MockTransaccion, _ctx

_HOY = date(2026, 11, 5)


def _ciclos() -> list[tuple[date, date]]:
    return [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), date(2026, 4, 30)),
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 7, 1), date(2026, 7, 31)),
        (date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 30)),
    ]


def test_t1_sueldos_temprano_basico():
    """T-1. Sueldos 1.000.000 el 30/09 y 1.050.000 el 30/10."""
    txs = [
        MockTransaccion(fecha=date(2026, 9, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 10, 30), monto=1050000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    # Con incluir_temprano=True
    res_true = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res_true.tipo == "temprano"
    assert res_true.monto == Decimal("1000000.00")
    assert res_true.es_temprano is True
    assert res_true.motivo_sin_datos is None
    assert len(res_true.fuentes) == 1
    assert res_true.fuentes[0].tipo == "temprano"

    # Con el valor por defecto
    res_def = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS")
    assert res_def.tipo == "sin_datos"
    assert res_def.motivo_sin_datos == "pocos_ciclos"
    assert res_def.es_temprano is False


def test_t2_un_solo_sueldo():
    """T-2. Un solo sueldo (30/10): con True, sin_datos."""
    txs = [
        MockTransaccion(fecha=date(2026, 10, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    res = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res.tipo == "sin_datos"
    assert res.es_temprano is False


def test_t3_sueldos_con_hueco():
    """T-3. Sueldos 30/08 y 30/10 (septiembre sin cobro): con True, sin_datos con motivo 'irregular'."""
    txs = [
        MockTransaccion(fecha=date(2026, 8, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 10, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    res = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res.tipo == "sin_datos"
    assert res.motivo_sin_datos == "irregular"
    assert res.es_temprano is False


def test_t4_sueldos_mucha_diferencia():
    """T-4. Sueldos 1.000.000 el 30/09 y 500.000 el 30/10: con True, sin_datos."""
    txs = [
        MockTransaccion(fecha=date(2026, 9, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 10, 30), monto=500000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    res = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res.tipo == "sin_datos"
    assert res.es_temprano is False


def test_t5_fuente_vieja():
    """T-5. Sueldos 30/03 y 30/04 nada más: con True, sin_datos (fuente vieja)."""
    txs = [
        MockTransaccion(fecha=date(2026, 3, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 4, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    res = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res.tipo == "sin_datos"
    assert res.es_temprano is False


def test_t6_cobro_ciclo_actual():
    """T-6. Sueldos 1.000.000 el 01/10 y 1.000.000 el 03/11 (ciclo actual): con True, tipo 'temprano', monto Decimal('1000000.00')."""
    txs = [
        MockTransaccion(fecha=date(2026, 10, 1), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 11, 3), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    res = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res.tipo == "temprano"
    assert res.monto == Decimal("1000000.00")
    assert res.es_temprano is True
    assert res.motivo_sin_datos is None


def test_t7_confirmado_manda():
    """T-7. Sueldo 1.000.000 el día 5 de cada mes de enero a octubre: con True, tipo 'regular', es_temprano False (lo confirmado manda)."""
    txs = [
        MockTransaccion(fecha=date(2026, m, 5), monto=1000000, categoria="Empleo", subcategoria="Sueldo")
        for m in range(1, 11)
    ]
    res = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res.tipo == "regular"
    assert res.es_temprano is False
    assert res.monto == Decimal("1000000")


def test_t8_cobros_personas_ignorado():
    """T-8. Sueldos temprano de T-1 más un cobro de 'Cobros de personas' el 30/09 y el 30/10 de 1.000.000 cada uno: con True, monto Decimal('1000000.00')."""
    txs = [
        MockTransaccion(fecha=date(2026, 9, 30), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 10, 30), monto=1050000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 9, 30), monto=1000000, categoria="Transferencias", subcategoria="Cobros de personas"),
        MockTransaccion(fecha=date(2026, 10, 30), monto=1000000, categoria="Transferencias", subcategoria="Cobros de personas"),
    ]
    res = calcular_ingreso_habitual_en_memoria(txs, _ciclos(), _HOY, _ctx(_HOY), moneda="ARS", incluir_temprano=True)
    assert res.tipo == "temprano"
    assert res.monto == Decimal("1000000.00")
    assert res.es_temprano is True

