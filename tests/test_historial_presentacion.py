# -*- coding: utf-8 -*-
"""
tests/test_historial_presentacion.py
Pruebas unitarias de presentación de movimientos (títulos e íconos).
"""
from __future__ import annotations

from datetime import date
from uuid import uuid4
from types import SimpleNamespace

from app.models.transaccion import MetodoPago
from app.services.historial_presentacion import (
    ContextoTitulos,
    titulo_base,
    titulo_movimiento,
    iconos_movimiento,
)


def test_titulo_base():
    assert titulo_base("  Café Martínez ", "Cafetería", "Comida") == "Café Martínez"
    assert titulo_base("", "Internet", "Servicios") == "Internet"
    assert titulo_base(None, None, "Comida") == "Comida"
    assert titulo_base(None, None, None) == "Movimiento"


def test_titulo_movimiento_espejo_reintegro():
    compra_id = uuid4()
    # Espejo de reintegro de una compra sin descripción con subcategoría "Internet" -> "Internet"
    ctx = ContextoTitulos(
        titulos_base_compras={compra_id: "Internet"}
    )
    tx_espejo = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=compra_id,
        tipo_espejo="reintegro",
        descripcion="Reintegro: Internet",
        movimiento_meta_id=None,
        es_cuota_hija=False,
        pago_resumen_vencimiento=None,
    )
    assert titulo_movimiento(tx_espejo, ctx) == "Internet"


def test_titulo_movimiento_espejo_parte():
    compra_id = uuid4()
    # Espejo "parte" de una compra "Cena con amigas" -> "Cena con amigas"
    ctx = ContextoTitulos(
        titulos_base_compras={compra_id: "Cena con amigas"}
    )
    tx_espejo = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=compra_id,
        tipo_espejo="parte",
        descripcion="Parte de: Cena con amigas",
        movimiento_meta_id=None,
        es_cuota_hija=False,
        pago_resumen_vencimiento=None,
    )
    assert titulo_movimiento(tx_espejo, ctx) == "Cena con amigas"


def test_titulo_movimiento_metas():
    # Aporte con meta "Viaje a Brasil" -> "Viaje a Brasil"
    mov_meta_id = uuid4()
    ctx = ContextoTitulos(
        nombres_metas={mov_meta_id: "Viaje a Brasil"}
    )
    tx_meta = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=mov_meta_id,
        descripcion="Aporte a la meta: Viaje a Brasil",
        es_cuota_hija=False,
        pago_resumen_vencimiento=None,
    )
    assert titulo_movimiento(tx_meta, ctx) == "Viaje a Brasil"

    # Aporte sin meta encontrada con descripción "Aporte a la meta: loco" -> "loco"
    tx_meta_sin_ctx = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=uuid4(),
        descripcion="Aporte a la meta: loco",
        es_cuota_hija=False,
        pago_resumen_vencimiento=None,
    )
    assert titulo_movimiento(tx_meta_sin_ctx, ContextoTitulos()) == "loco"

    # Retiro "Retiro de la meta: loco" -> "loco"
    tx_retiro_sin_ctx = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=uuid4(),
        descripcion="Retiro de la meta: loco",
        es_cuota_hija=False,
        pago_resumen_vencimiento=None,
    )
    assert titulo_movimiento(tx_retiro_sin_ctx, ContextoTitulos()) == "loco"


def test_titulo_movimiento_cuota_hija():
    tx_id = uuid4()
    grupo_id = uuid4()
    # Cuota hija 2 de 12 con padre "Heladera" -> "Heladera 2/12"
    ctx = ContextoTitulos(
        padres_desc_map={grupo_id: "Heladera"},
        cuotas_info={tx_id: (2, 12)},
    )
    tx_cuota = SimpleNamespace(
        id=tx_id,
        reintegro_de_id=None,
        movimiento_meta_id=None,
        es_cuota_hija=True,
        grupo_cuotas_id=grupo_id,
        descripcion="Heladera 2/12",
        pago_resumen_vencimiento=None,
    )
    assert titulo_movimiento(tx_cuota, ctx) == "Heladera 2/12"


def test_titulo_movimiento_pago_resumen_y_gasto_comun():
    # Pago de resumen sin descripción -> "Pago de tarjeta"
    tx_pago = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=None,
        es_cuota_hija=False,
        pago_resumen_vencimiento="2026-11-03",
        descripcion="",
    )
    assert titulo_movimiento(tx_pago, ContextoTitulos()) == "Pago de tarjeta"

    # Gasto común "Súper" -> "Súper"
    tx_comun = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=None,
        es_cuota_hija=False,
        pago_resumen_vencimiento=None,
        descripcion="Súper",
    )
    assert titulo_movimiento(tx_comun, ContextoTitulos()) == "Súper"


def test_titulo_pago_tarjeta_y_percepcion():
    tarjeta_id = uuid4()
    ctx = ContextoTitulos(
        nombres_tarjetas={tarjeta_id: "Visa Galicia"}
    )

    # 1. Pago con tarjeta "Visa Galicia" y descripción guardada "Pago resumen icia" -> "Pago de tarjeta Visa Galicia"
    tx_pago = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=None,
        es_cuota_hija=False,
        tarjeta_id=tarjeta_id,
        pago_resumen_vencimiento=date(2026, 11, 3),
        pago_origen_id=None,
        descripcion="Pago resumen icia",
    )
    assert titulo_movimiento(tx_pago, ctx) == "Pago de tarjeta Visa Galicia"

    # 2. Percepción de esa tarjeta con descripción "Percepción compras exterior (30%) - Pago resumen icia" -> "Percepción Visa Galicia"
    tx_percep = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=None,
        es_cuota_hija=False,
        tarjeta_id=tarjeta_id,
        pago_resumen_vencimiento=None,
        pago_origen_id=tx_pago.id,
        descripcion="Percepción compras exterior (30%) - Pago resumen icia",
    )
    assert titulo_movimiento(tx_percep, ctx) == "Percepción Visa Galicia"

    # 3. Pago sin tarjeta en el mapa y descripción vacía -> "Pago de tarjeta"
    tx_pago_sin_mapa = SimpleNamespace(
        id=uuid4(),
        reintegro_de_id=None,
        movimiento_meta_id=None,
        es_cuota_hija=False,
        tarjeta_id=uuid4(),
        pago_resumen_vencimiento=date(2026, 11, 3),
        pago_origen_id=None,
        descripcion="",
    )
    assert titulo_movimiento(tx_pago_sin_mapa, ctx) == "Pago de tarjeta"


def test_iconos_movimiento():
    # Compra con crédito -> ["credito"]
    tx_cred = SimpleNamespace(reintegro_de_id=None, tipo_espejo=None, suscripcion_id=None, metodo_pago=MetodoPago.CREDITO)
    assert iconos_movimiento(tx_cred, "Gasto") == ["credito"]

    # Débito -> []
    tx_deb = SimpleNamespace(reintegro_de_id=None, tipo_espejo=None, suscripcion_id=None, metodo_pago=MetodoPago.DEBITO)
    assert iconos_movimiento(tx_deb, "Gasto") == []

    # Suscripción con crédito -> ["suscripcion", "credito"]
    tx_susc_cred = SimpleNamespace(reintegro_de_id=None, tipo_espejo=None, suscripcion_id=uuid4(), metodo_pago=MetodoPago.CREDITO)
    assert iconos_movimiento(tx_susc_cred, "Gasto") == ["suscripcion", "credito"]

    # Suscripción con débito -> ["suscripcion"]
    tx_susc_deb = SimpleNamespace(reintegro_de_id=None, tipo_espejo=None, suscripcion_id=uuid4(), metodo_pago=MetodoPago.DEBITO)
    assert iconos_movimiento(tx_susc_deb, "Gasto") == ["suscripcion"]

    # Espejo reintegro con crédito -> ["reintegro", "credito"]
    tx_esp_cred = SimpleNamespace(reintegro_de_id=uuid4(), tipo_espejo="reintegro", suscripcion_id=None, metodo_pago=MetodoPago.CREDITO)
    assert iconos_movimiento(tx_esp_cred, "Gasto") == ["reintegro", "credito"]

    # Espejo parte -> ["devolucion"]
    tx_esp_parte = SimpleNamespace(reintegro_de_id=uuid4(), tipo_espejo="parte", suscripcion_id=None, metodo_pago=MetodoPago.DEBITO)
    assert iconos_movimiento(tx_esp_parte, "Gasto") == ["devolucion"]

    # Etiqueta "Pasó por mi cuenta" -> ["paso_por_mi_cuenta"]
    tx_paso = SimpleNamespace(reintegro_de_id=None, tipo_espejo=None, suscripcion_id=None, metodo_pago=MetodoPago.DEBITO)
    assert iconos_movimiento(tx_paso, "Pasó por mi cuenta") == ["paso_por_mi_cuenta"]

    # Etiqueta "Inversión" con suscripción y crédito -> ["inversion", "suscripcion"] (máximo 2)
    tx_inv_susc_cred = SimpleNamespace(reintegro_de_id=None, tipo_espejo=None, suscripcion_id=uuid4(), metodo_pago=MetodoPago.CREDITO)
    assert iconos_movimiento(tx_inv_susc_cred, "Inversión") == ["inversion", "suscripcion"]

