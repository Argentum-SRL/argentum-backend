"""
Tests unitarios del detector de gastos fijos (Fase 4d1).
Ubicación: tests/motor/test_patrones.py

Cubre los 13 casos de prueba canónicos (T1 a T13):
T1. Mensual fuerte (alquiler con 4 ocurrencias, presencia 1.00, dispersión 0).
T2. Mensual débil (alquiler con 2 ocurrencias, presencia 1.00).
T3. Gasto frecuente descartado por varias ocurrencias por período (café cada 7 días).
T4. Bimestral fuerte (luz cada dos meses, 4 ocurrencias, presencia 1.00).
T5. Mensual con mes faltante (5 ocurrencias en 6 períodos, presencia 0.83).
T6. Mensual con montos dispares descartado por dispersión alta.
T7. Suscripción declarada excluida sin fijos ni descartados.
T8. Cuota hija excluida sin fijos ni descartados.
T9. Serie inactiva descartada por terminada.
T10. Monedas separadas (ARS y USD detectadas en paralelo).
T11. Sin descripción agrupado por subcategoría canónica.
T12. Mismo comercio con dos importes distintos partido por monto (primero el mayor).
T13. Anual fuerte con dos cobros consecutivos.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion
from app.models.usuario import Moneda
from app.services.definiciones_service import ContextoDefiniciones
from app.utils.patrones import detectar_fijos


class MockCategoria:
    def __init__(self, id: str, nombre: str):
        self.id = id
        self.nombre = nombre


class MockSubcategoria:
    def __init__(self, id: str, nombre: str):
        self.id = id
        self.nombre = nombre


class MockTransaccion:
    """Movimiento financiero sintético para pruebas del motor de patrones."""

    def __init__(
        self,
        fecha: date,
        monto: Decimal | int | str,
        moneda: Moneda = Moneda.ARS,
        descripcion: str = "",
        categoria_id: str | None = "cat-servicios",
        subcategoria_id: str | None = "sub-general",
        billetera_id: str = "billetera_principal",
        tipo: TipoTransaccion = TipoTransaccion.EGRESO,
        estado_verificacion: EstadoVerificacionTransaccion = EstadoVerificacionTransaccion.CONFIRMADA,
        suscripcion_id: UUID | None = None,
        es_cuota_hija: bool = False,
        es_padre_cuotas: bool = False,
        movimiento_meta_id: Any = None,
        pago_resumen_vencimiento: Any = None,
        id: str | None = None,
    ):
        self.id = id or str(uuid4())
        self.fecha = fecha
        self.monto = Decimal(str(monto))
        self.moneda = moneda
        self.descripcion = descripcion
        self.categoria_id = categoria_id
        self.subcategoria_id = subcategoria_id
        self.billetera_id = billetera_id
        self.tipo = tipo
        self.estado_verificacion = estado_verificacion
        self.suscripcion_id = suscripcion_id
        self.es_cuota_hija = es_cuota_hija
        self.es_padre_cuotas = es_padre_cuotas
        self.movimiento_meta_id = movimiento_meta_id
        self.pago_resumen_vencimiento = pago_resumen_vencimiento


@pytest.fixture
def ctx_vacio() -> ContextoDefiniciones:
    """Contexto de definiciones neutro con fecha de referencia 2026-09-20."""
    return ContextoDefiniciones(
        grupos_cuotas_cantidades={},
        billeteras_inversion_ids=set(),
        categoria_ahorro_ids=set(),
        subcategoria_tarjeta_id=None,
        subcategoria_tarjeta_ids=set(),
        hoy=date(2026, 9, 20),
    )


@pytest.fixture
def ipc_plano() -> dict[str, Decimal]:
    """Mapa de IPC constante para aislar deflactación nominal en pruebas."""
    meses = [
        f"{y:04d}-{m:02d}"
        for y in range(2024, 2028)
        for m in range(1, 13)
    ]
    return {m: Decimal("100.0") for m in meses}


def test_t01_alquiler_mensual_fuerte(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T1: Alquiler $400.000 mensual 4 meses seguidos es fuerte con presencia 1.00 y dispersión 0."""
    txs = [
        MockTransaccion(date(2026, 6, 5), 400000, descripcion="Alquiler departamento"),
        MockTransaccion(date(2026, 7, 5), 400000, descripcion="Alquiler departamento"),
        MockTransaccion(date(2026, 8, 5), 400000, descripcion="Alquiler departamento"),
        MockTransaccion(date(2026, 9, 5), 400000, descripcion="Alquiler departamento"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.frecuencia == "mensual"
    assert fijo.fuerza == "fuerte"
    assert fijo.ocurrencias == 4
    assert fijo.presencia == Decimal("1.00")
    assert fijo.dispersion_monto == Decimal("0.0000")
    assert fijo.dia_tipico == 5
    assert fijo.proxima_fecha == date(2026, 10, 5)


def test_t02_alquiler_mensual_debil(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T2: Alquiler con solo 2 ocurrencias es mensual débil (requiere 3 para fuerte)."""
    txs = [
        MockTransaccion(date(2026, 8, 5), 400000, descripcion="Alquiler departamento"),
        MockTransaccion(date(2026, 9, 5), 400000, descripcion="Alquiler departamento"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.frecuencia == "mensual"
    assert fijo.fuerza == "debil"
    assert fijo.ocurrencias == 2
    assert fijo.presencia == Decimal("1.00")


def test_t03_cafe_frecuente_descartado_varias_por_periodo(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T3: Café cada 7 días, 8 veces desde 2026-08-01: 0 fijos, descartado por varias_por_periodo."""
    from datetime import timedelta
    inicio = date(2026, 8, 1)
    txs = [
        MockTransaccion(inicio + timedelta(days=7 * i), 3000, descripcion="Café Martínez")
        for i in range(8)
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 0
    assert len(res.descartados) == 1
    desc = res.descartados[0]
    assert desc.motivo == "varias_por_periodo"
    assert desc.ocurrencias == 8


def test_t04_luz_bimestral_fuerte(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T4: Luz $20.000 el 10/03, 10/05, 10/07 y 10/09: bimestral, fuerte, presencia 1.00, día 10, próxima 2026-11-10."""
    txs = [
        MockTransaccion(date(2026, 3, 10), 20000, descripcion="Edesur Luz"),
        MockTransaccion(date(2026, 5, 10), 20000, descripcion="Edesur Luz"),
        MockTransaccion(date(2026, 7, 10), 20000, descripcion="Edesur Luz"),
        MockTransaccion(date(2026, 9, 10), 20000, descripcion="Edesur Luz"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.frecuencia == "bimestral"
    assert fijo.fuerza == "fuerte"
    assert fijo.presencia == Decimal("1.00")
    assert fijo.dia_tipico == 10
    assert fijo.proxima_fecha == date(2026, 11, 10)


def test_t05_mensual_con_omision_junio(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T5: $50.000 el 05/04, 05/05, 05/07, 05/08 y 05/09 (falta junio): mensual, fuerte, presencia 0.83."""
    txs = [
        MockTransaccion(date(2026, 4, 5), 50000, descripcion="Gimnasio Cuota"),
        MockTransaccion(date(2026, 5, 5), 50000, descripcion="Gimnasio Cuota"),
        MockTransaccion(date(2026, 7, 5), 50000, descripcion="Gimnasio Cuota"),
        MockTransaccion(date(2026, 8, 5), 50000, descripcion="Gimnasio Cuota"),
        MockTransaccion(date(2026, 9, 5), 50000, descripcion="Gimnasio Cuota"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.frecuencia == "mensual"
    assert fijo.fuerza == "fuerte"
    assert fijo.presencia == Decimal("0.83")


def test_t06_monto_distinto_descartado(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T6: Mensual el 05/06, 05/07 y 05/08 por 100.000, 160.000 y 60.000: Descartado 'monto_distinto'."""
    txs = [
        MockTransaccion(date(2026, 6, 5), 100000, descripcion="Comercio X"),
        MockTransaccion(date(2026, 7, 5), 160000, descripcion="Comercio X"),
        MockTransaccion(date(2026, 8, 5), 60000, descripcion="Comercio X"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 0
    assert len(res.descartados) == 1
    assert res.descartados[0].motivo == "monto_distinto"


def test_t07_suscripcion_declarada_excluida(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T7: T1 con suscripcion_id cargado: sin fijos ni descartados."""
    sub_id = uuid4()
    txs = [
        MockTransaccion(date(2026, 6, 5), 400000, descripcion="Alquiler departamento", suscripcion_id=sub_id),
        MockTransaccion(date(2026, 7, 5), 400000, descripcion="Alquiler departamento", suscripcion_id=sub_id),
        MockTransaccion(date(2026, 8, 5), 400000, descripcion="Alquiler departamento", suscripcion_id=sub_id),
        MockTransaccion(date(2026, 9, 5), 400000, descripcion="Alquiler departamento", suscripcion_id=sub_id),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 0
    assert len(res.descartados) == 0


def test_t08_cuota_hija_excluida(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T8: T1 con es_cuota_hija True: sin fijos ni descartados."""
    txs = [
        MockTransaccion(date(2026, 6, 5), 400000, descripcion="Alquiler departamento", es_cuota_hija=True),
        MockTransaccion(date(2026, 7, 5), 400000, descripcion="Alquiler departamento", es_cuota_hija=True),
        MockTransaccion(date(2026, 8, 5), 400000, descripcion="Alquiler departamento", es_cuota_hija=True),
        MockTransaccion(date(2026, 9, 5), 400000, descripcion="Alquiler departamento", es_cuota_hija=True),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 0
    assert len(res.descartados) == 0


def test_t09_terminado_descartado(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T9: Mensual el 05/03, 05/04 y 05/05 frente a fecha_destino 2026-09-20: Descartado 'terminado'."""
    txs = [
        MockTransaccion(date(2026, 3, 5), 50000, descripcion="Servicio Antiguo"),
        MockTransaccion(date(2026, 4, 5), 50000, descripcion="Servicio Antiguo"),
        MockTransaccion(date(2026, 5, 5), 50000, descripcion="Servicio Antiguo"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 0
    assert len(res.descartados) == 1
    assert res.descartados[0].motivo == "terminado"


def test_t10_monedas_separadas(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T10: 'Netflix' el 12/06, 12/07 y 12/08, tres en ARS y tres en USD: 2 fijos, uno por moneda."""
    txs = [
        MockTransaccion(date(2026, 6, 12), 10000, moneda=Moneda.ARS, descripcion="Netflix Streaming"),
        MockTransaccion(date(2026, 7, 12), 10000, moneda=Moneda.ARS, descripcion="Netflix Streaming"),
        MockTransaccion(date(2026, 8, 12), 10000, moneda=Moneda.ARS, descripcion="Netflix Streaming"),
        MockTransaccion(date(2026, 6, 12), 15, moneda=Moneda.USD, descripcion="Netflix Streaming"),
        MockTransaccion(date(2026, 7, 12), 15, moneda=Moneda.USD, descripcion="Netflix Streaming"),
        MockTransaccion(date(2026, 8, 12), 15, moneda=Moneda.USD, descripcion="Netflix Streaming"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 2
    monedas = {f.moneda for f in res.fijos}
    assert monedas == {Moneda.ARS, Moneda.USD}


def test_t11_descripcion_vacia_agrupa_por_subcategoria(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T11: Descripción '' con la misma subcategoría el 05/06, 05/07 y 05/08: 1 fijo con clave 'sub:<id>'."""
    sub_id = "sub-expensas-999"
    txs = [
        MockTransaccion(date(2026, 6, 5), 80000, descripcion="", subcategoria_id=sub_id),
        MockTransaccion(date(2026, 7, 5), 80000, descripcion="", subcategoria_id=sub_id),
        MockTransaccion(date(2026, 8, 5), 80000, descripcion="", subcategoria_id=sub_id),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 1
    assert res.fijos[0].clave == f"sub:{sub_id}"


def test_t12_particion_por_monto_mismo_comercio(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T12: 'Personal' $20.000 los días 10 y 'Personal' $40.000 los días 15 de junio, julio y agosto: 2 fijos fuertes."""
    txs = [
        MockTransaccion(date(2026, 6, 10), 20000, descripcion="Personal Celular"),
        MockTransaccion(date(2026, 6, 15), 40000, descripcion="Personal Celular"),
        MockTransaccion(date(2026, 7, 10), 20000, descripcion="Personal Celular"),
        MockTransaccion(date(2026, 7, 15), 40000, descripcion="Personal Celular"),
        MockTransaccion(date(2026, 8, 10), 20000, descripcion="Personal Celular"),
        MockTransaccion(date(2026, 8, 15), 40000, descripcion="Personal Celular"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 2
    assert res.fijos[0].fuerza == "fuerte"
    assert res.fijos[1].fuerza == "fuerte"
    # Ordenados de mayor a menor monto mediano
    assert res.fijos[0].monto_mediano_deflactado == Decimal("40000.00")
    assert res.fijos[1].monto_mediano_deflactado == Decimal("20000.00")


def test_t13_anual_fuerte(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T13: $120.000 el 01/03/2025 y el 02/03/2026: anual, fuerte, próxima 2027-03-02."""
    txs = [
        MockTransaccion(date(2025, 3, 1), 120000, descripcion="Seguro Anual"),
        MockTransaccion(date(2026, 3, 2), 120000, descripcion="Seguro Anual"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)

    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.frecuencia == "anual"
    assert fijo.fuerza == "fuerte"
    assert fijo.proxima_fecha == date(2027, 3, 2)
