"""
Tests unitarios determinísticos para cotizaciones del dólar:
a. Guardar dos veces el mismo día deja una sola fila por tipo, con los valores de la segunda vez (upsert idempotente).
b. obtener_cotizacion_por_fecha, sin fila del día, devuelve la del día anterior y no llama a la API externa.
c. Con la tabla vacía, devuelve None y emite el aviso esperado.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.cotizacion_dolar import CotizacionDolar
from app.services.dolar_service import (
    guardar_cotizaciones_del_dia,
    obtener_cotizacion_por_fecha,
)
from app.utils.fecha import hoy_argentina


@pytest.fixture(name="db_session")
def fixture_db_session():
    """
    Inicializa una sesión de base de datos aislada en memoria (SQLite)
    con la tabla de cotizaciones_dolar para pruebas unitarias determinísticas.
    """
    engine = create_engine("sqlite:///:memory:")
    CotizacionDolar.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_guardar_dos_veces_mismo_dia_deja_una_fila_con_valores_segunda_vez(db_session):
    """
    a. Guardar dos veces el mismo día deja una sola fila por tipo, con los valores de la segunda vez.
    Verifica que guardar_cotizaciones_del_dia sea un upsert idempotente que actualiza
    los montos sin duplicar registros.
    """
    payload_primera_vez = {
        "cotizaciones": {
            "blue": {"compra": 1400.0, "venta": 1420.0, "promedio": 1410.0},
            "oficial": {"compra": 950.0, "venta": 990.0, "promedio": 970.0},
            "mep": {"compra": 1380.0, "venta": 1395.0, "promedio": 1387.5},
            "tarjeta": {"compra": 1500.0, "venta": 1550.0, "promedio": 1525.0},
        }
    }
    payload_segunda_vez = {
        "cotizaciones": {
            "blue": {"compra": 1450.0, "venta": 1475.0, "promedio": 1462.5},
            "oficial": {"compra": 955.0, "venta": 995.0, "promedio": 975.0},
            "mep": {"compra": 1390.0, "venta": 1405.0, "promedio": 1397.5},
            "tarjeta": {"compra": 1510.0, "venta": 1560.0, "promedio": 1535.0},
        }
    }

    # Primera sincronización
    with patch("app.services.dolar_service.get_cotizaciones_dolar", return_value=payload_primera_vez):
        resultado_1 = guardar_cotizaciones_del_dia(db_session)
    assert len(resultado_1) == 4

    # Segunda sincronización en el mismo día con valores actualizados
    with patch("app.services.dolar_service.get_cotizaciones_dolar", return_value=payload_segunda_vez):
        resultado_2 = guardar_cotizaciones_del_dia(db_session)
    assert len(resultado_2) == 4

    # Verificar que en la base de datos hay exactamente una fila por tipo
    todas = db_session.execute(select(CotizacionDolar)).scalars().all()
    assert len(todas) == 4, f"Se esperaban 4 filas en total, pero se encontraron {len(todas)}"

    tipos_encontrados = {c.tipo for c in todas}
    assert tipos_encontrados == {"blue", "oficial", "mep", "tarjeta"}

    # Verificar que los valores persistidos correspondan a los de la segunda vez
    por_tipo = {c.tipo: c for c in todas}
    assert por_tipo["blue"].compra == Decimal("1450.0000")
    assert por_tipo["blue"].venta == Decimal("1475.0000")
    assert por_tipo["blue"].promedio == Decimal("1462.5000")

    assert por_tipo["oficial"].compra == Decimal("955.0000")
    assert por_tipo["oficial"].venta == Decimal("995.0000")

    assert por_tipo["mep"].compra == Decimal("1390.0000")
    assert por_tipo["mep"].venta == Decimal("1405.0000")

    assert por_tipo["tarjeta"].compra == Decimal("1510.0000")
    assert por_tipo["tarjeta"].venta == Decimal("1560.0000")


def test_obtener_cotizacion_sin_fila_del_dia_devuelve_anterior_y_no_llama_api(db_session):
    """
    b. obtener_cotizacion_por_fecha, sin fila del día, devuelve la del día anterior y no llama a la API.
    Se verifica con un mock que lanza una excepción si se intenta llamar a la API externa.
    """
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    # Insertamos cotización únicamente para el día anterior
    cot_ayer = CotizacionDolar(
        fecha=ayer,
        tipo="blue",
        compra=Decimal("1410.0"),
        venta=Decimal("1430.0"),
        promedio=Decimal("1420.0"),
    )
    db_session.add(cot_ayer)
    db_session.commit()

    # Mock que falla si cualquier componente intenta llamar a get_cotizaciones_dolar o HTTP
    with patch(
        "app.services.dolar_service.get_cotizaciones_dolar",
        side_effect=AssertionError("La API externa jamás debe ser llamada por obtener_cotizacion_por_fecha"),
    ), patch(
        "httpx.Client.get",
        side_effect=AssertionError("No deben realizarse llamadas HTTP"),
    ):
        cot = obtener_cotizacion_por_fecha(db_session, "blue", hoy)

    # Debe devolver la cotización del día anterior más cercana
    assert cot is not None, "La cotización anterior debió ser devuelta como fallback"
    assert cot.fecha == ayer, f"Se esperaba fecha {ayer}, pero se obtuvo {cot.fecha}"
    assert cot.tipo == "blue"
    assert cot.compra == Decimal("1410.0000")
    assert cot.venta == Decimal("1430.0000")


def test_obtener_cotizacion_tabla_vacia_devuelve_none_y_emite_aviso(db_session, caplog):
    """
    c. Con la tabla vacía, devuelve None y emite el aviso de advertencia esperado.
    """
    hoy = hoy_argentina()

    # Asegurar que la tabla está completamente vacía
    filas = db_session.execute(select(CotizacionDolar)).scalars().all()
    assert len(filas) == 0

    with caplog.at_level(logging.WARNING, logger="app.services.dolar_service"):
        cot = obtener_cotizacion_por_fecha(db_session, "blue", hoy)

    # Debe devolver None de forma explícita
    assert cot is None, "Con la tabla vacía, debe retornar None"

    # Verificar emisión del aviso en el logger
    avisos = [
        rec.message
        for rec in caplog.records
        if "No se encontró cotización histórica para tipo 'blue'" in rec.message
    ]
    assert len(avisos) >= 1, "Se esperaba un aviso de cotización histórica no encontrada en el logger"
