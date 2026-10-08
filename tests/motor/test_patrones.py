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

from dateutil.relativedelta import relativedelta
import pytest

from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion
from app.models.usuario import Moneda
from app.services.definiciones_service import ContextoDefiniciones
from app.utils.patrones import clasificar_cajas, detectar_fijos, rubro_de



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
        categoria: MockCategoria | None = None,
        subcategoria: MockSubcategoria | None = None,
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
        self.categoria = categoria
        self.subcategoria = subcategoria



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


def test_t14_alquiler_distancia_corta_febrero_marzo(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T14. Alquiler $400.000 el 10/02, 01/03, 08/04, 05/05 y 03/06/2026, destino 2026-06-20: 1 fijo mensual fuerte."""
    txs = [
        MockTransaccion(date(2026, 2, 10), 400000, descripcion="Alquiler"),
        MockTransaccion(date(2026, 3, 1), 400000, descripcion="Alquiler"),
        MockTransaccion(date(2026, 4, 8), 400000, descripcion="Alquiler"),
        MockTransaccion(date(2026, 5, 5), 400000, descripcion="Alquiler"),
        MockTransaccion(date(2026, 6, 3), 400000, descripcion="Alquiler"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 6, 20), ctx=ctx_vacio)
    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.frecuencia == "mensual"
    assert fijo.fuerza == "fuerte"


def test_t15_clase_tenis_cada_14_dias_descartada(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T15. 'Clase de tenis' $20.000 cada 14 días desde 2026-06-01, 6 veces, destino 2026-08-20: 0 fijos."""
    txs = [
        MockTransaccion(date(2026, 6, 1) + relativedelta(days=14 * i), 20000, descripcion="Clase de tenis")
        for i in range(6)
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 8, 20), ctx=ctx_vacio)
    assert len(res.fijos) == 0


def test_t16_alquiler_proxima_fecha_dia_tipico(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T16. Alquiler $400.000 el 05/06, 05/07, 05/08 y 09/09/2026, destino 2026-09-20: dia_tipico 5 y próxima 2026-10-05."""
    txs = [
        MockTransaccion(date(2026, 6, 5), 400000, descripcion="Alquiler"),
        MockTransaccion(date(2026, 7, 5), 400000, descripcion="Alquiler"),
        MockTransaccion(date(2026, 8, 5), 400000, descripcion="Alquiler"),
        MockTransaccion(date(2026, 9, 9), 400000, descripcion="Alquiler"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 9, 20), ctx=ctx_vacio)
    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.dia_tipico == 5
    assert fijo.proxima_fecha == date(2026, 10, 5)


def test_t17_abono_fin_de_mes_febrero(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T17. $30.000 el 30/11/2025, 31/12/2025 y 31/01/2026, destino 2026-02-10: dia_tipico 31 y próxima 2026-02-28."""
    txs = [
        MockTransaccion(date(2025, 11, 30), 30000, descripcion="Abono"),
        MockTransaccion(date(2025, 12, 31), 30000, descripcion="Abono"),
        MockTransaccion(date(2026, 1, 31), 30000, descripcion="Abono"),
    ]
    res = detectar_fijos(txs, ipc_plano, date(2026, 2, 10), ctx=ctx_vacio)
    assert len(res.fijos) == 1
    fijo = res.fijos[0]
    assert fijo.dia_tipico == 31
    assert fijo.proxima_fecha == date(2026, 2, 28)


def test_t18_rubro_de():
    """T18. Clasificación con rubro_de para casos definidos."""
    # Delivery en Gastronomía: costumbre
    tx_del = MockTransaccion(
        date(2026, 6, 1), 1000,
        categoria=MockCategoria("c1", "Gastronomía"),
        subcategoria=MockSubcategoria("s1", "Delivery"),
    )
    assert rubro_de(tx_del) == "costumbre"

    # Taxi / Apps en Transporte: costumbre
    tx_taxi = MockTransaccion(
        date(2026, 6, 1), 1000,
        categoria=MockCategoria("c2", "Transporte"),
        subcategoria=MockSubcategoria("s2", "Taxi / Apps"),
    )
    assert rubro_de(tx_taxi) == "costumbre"

    # Combustible en Transporte: dia_a_dia
    tx_comb = MockTransaccion(
        date(2026, 6, 1), 1000,
        categoria=MockCategoria("c2", "Transporte"),
        subcategoria=MockSubcategoria("s3", "Combustible"),
    )
    assert rubro_de(tx_comb) == "dia_a_dia"

    # Verdulería en Alimentación: dia_a_dia
    tx_verd = MockTransaccion(
        date(2026, 6, 1), 1000,
        categoria=MockCategoria("c3", "Alimentación"),
        subcategoria=MockSubcategoria("s4", "Verdulería"),
    )
    assert rubro_de(tx_verd) == "dia_a_dia"

    # Ferretería en Hogar: dia_a_dia
    tx_ferr = MockTransaccion(
        date(2026, 6, 1), 1000,
        categoria=MockCategoria("c4", "Hogar"),
        subcategoria=MockSubcategoria("s5", "Ferretería"),
    )
    assert rubro_de(tx_ferr) == "dia_a_dia"

    # Sin subcategoría, en Indumentaria: dia_a_dia
    tx_indum = MockTransaccion(
        date(2026, 6, 1), 1000,
        categoria=MockCategoria("c5", "Indumentaria"),
        subcategoria=None,
    )
    assert rubro_de(tx_indum) == "dia_a_dia"

    # Sin categoría ni subcategoría: dia_a_dia
    tx_nada = MockTransaccion(
        date(2026, 6, 1), 1000,
        categoria=None,
        subcategoria=None,
    )
    assert rubro_de(tx_nada) == "dia_a_dia"


def test_t19_clasificar_cajas_costumbre_delivery(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T19. Delivery $10.000 el 03, 10, 17 y 24/06, el 08 y 22/07 y el 05, 15 y 25/08/2026. Destino 2026-09-05."""
    fechas = [
        date(2026, 6, 3), date(2026, 6, 10), date(2026, 6, 17), date(2026, 6, 24),
        date(2026, 7, 8), date(2026, 7, 22),
        date(2026, 8, 5), date(2026, 8, 15), date(2026, 8, 25),
    ]
    sub_del = MockSubcategoria("s-del", "Delivery")
    cat_gast = MockCategoria("c-gast", "Gastronomía")
    txs = [
        MockTransaccion(
            f, 10000,
            descripcion="Delivery",
            categoria=cat_gast,
            subcategoria=sub_del,
            categoria_id=cat_gast.id,
            subcategoria_id=sub_del.id,
        )
        for f in fechas
    ]
    res = clasificar_cajas(txs, ipc_plano, date(2026, 9, 5), ctx=ctx_vacio)

    assert len(res.costumbre) == 1
    g = res.costumbre[0]
    assert g.ocurrencias == 9
    assert g.meses_con_movimiento == 3
    assert g.monto_mensual_mediano == Decimal("30000.00")
    assert len(res.dia_a_dia) == 0


def test_t20_clasificar_cajas_dia_a_dia_supermercado(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T20. Supermercado $25.000 todos los sábados de junio a agosto de 2026, más un delivery de $12.000 el 15/07."""
    sub_sup = MockSubcategoria("s-sup", "Supermercado")
    cat_alim = MockCategoria("c-alim", "Alimentación")
    sub_del = MockSubcategoria("s-del", "Delivery")
    cat_gast = MockCategoria("c-gast", "Gastronomía")

    # Sábados de junio, julio y agosto 2026
    sabados = [
        date(2026, 6, 6), date(2026, 6, 13), date(2026, 6, 20), date(2026, 6, 27),
        date(2026, 7, 4), date(2026, 7, 11), date(2026, 7, 18), date(2026, 7, 25),
        date(2026, 8, 1), date(2026, 8, 8), date(2026, 8, 15), date(2026, 8, 22), date(2026, 8, 29),
    ]
    txs = [
        MockTransaccion(
            f, 25000,
            descripcion="Coto Supermercado",
            categoria=cat_alim,
            subcategoria=sub_sup,
            categoria_id=cat_alim.id,
            subcategoria_id=sub_sup.id,
        )
        for f in sabados
    ]
    txs.append(
        MockTransaccion(
            date(2026, 7, 15), 12000,
            descripcion="Delivery Sushi",
            categoria=cat_gast,
            subcategoria=sub_del,
            categoria_id=cat_gast.id,
            subcategoria_id=sub_del.id,
        )
    )
    res = clasificar_cajas(txs, ipc_plano, date(2026, 9, 5), ctx=ctx_vacio)

    assert len(res.dia_a_dia) == 1
    assert res.dia_a_dia[0].nombre == "Supermercado"
    assert res.dia_a_dia[0].ocurrencias == 13
    assert len(res.costumbre) == 0


def test_t21_alquiler_fijo_y_delivery_costumbre(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T21. Alquiler $400.000 el 05/06, 05/07 y 05/08/2026, más el delivery de T19. Destino 2026-09-05."""
    sub_alq = MockSubcategoria("s-alq", "Alquiler")
    cat_viv = MockCategoria("c-viv", "Vivienda")
    sub_del = MockSubcategoria("s-del", "Delivery")
    cat_gast = MockCategoria("c-gast", "Gastronomía")

    txs_alq = [
        MockTransaccion(
            f, 400000,
            descripcion="Alquiler Depto",
            categoria=cat_viv,
            subcategoria=sub_alq,
            categoria_id=cat_viv.id,
            subcategoria_id=sub_alq.id,
        )
        for f in [date(2026, 6, 5), date(2026, 7, 5), date(2026, 8, 5)]
    ]
    fechas_del = [
        date(2026, 6, 3), date(2026, 6, 10), date(2026, 6, 17), date(2026, 6, 24),
        date(2026, 7, 8), date(2026, 7, 22),
        date(2026, 8, 5), date(2026, 8, 15), date(2026, 8, 25),
    ]
    txs_del = [
        MockTransaccion(
            f, 10000,
            descripcion="Delivery",
            categoria=cat_gast,
            subcategoria=sub_del,
            categoria_id=cat_gast.id,
            subcategoria_id=sub_del.id,
        )
        for f in fechas_del
    ]
    res = clasificar_cajas(txs_alq + txs_del, ipc_plano, date(2026, 9, 5), ctx=ctx_vacio)

    assert len(res.fijos) == 1
    assert len(res.costumbre) == 1
    assert res.costumbre[0].nombre == "Delivery"
    assert len(res.dia_a_dia) == 0


def test_t22_movimientos_fuera_de_ventana_no_califican(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T22. Delivery $10.000 el 02, 09 y 16/02 y el 02 y 09/03/2026, destino 2026-09-05: costumbre vacía."""
    sub_del = MockSubcategoria("s-del", "Delivery")
    cat_gast = MockCategoria("c-gast", "Gastronomía")
    fechas = [
        date(2026, 2, 2), date(2026, 2, 9), date(2026, 2, 16),
        date(2026, 3, 2), date(2026, 3, 9),
    ]
    txs = [
        MockTransaccion(
            f, 10000,
            descripcion="Delivery",
            categoria=cat_gast,
            subcategoria=sub_del,
            categoria_id=cat_gast.id,
            subcategoria_id=sub_del.id,
        )
        for f in fechas
    ]
    res = clasificar_cajas(txs, ipc_plano, date(2026, 9, 5), ctx=ctx_vacio)

    assert len(res.costumbre) == 0


def test_t23_dos_ocurrencias_no_repite(ctx_vacio: ContextoDefiniciones, ipc_plano: dict[str, Decimal]):
    """T23. Delivery $8.000 el 10/06 y $15.000 el 10/07/2026, destino 2026-09-05: 0 fijos y costumbre vacía."""
    sub_del = MockSubcategoria("s-del", "Delivery")
    cat_gast = MockCategoria("c-gast", "Gastronomía")
    txs = [
        MockTransaccion(
            date(2026, 6, 10), 8000,
            descripcion="Delivery Pizza",
            categoria=cat_gast,
            subcategoria=sub_del,
            categoria_id=cat_gast.id,
            subcategoria_id=sub_del.id,
        ),
        MockTransaccion(
            date(2026, 7, 10), 15000,
            descripcion="Delivery Sushi",
            categoria=cat_gast,
            subcategoria=sub_del,
            categoria_id=cat_gast.id,
            subcategoria_id=sub_del.id,
        ),
    ]
    res = clasificar_cajas(txs, ipc_plano, date(2026, 9, 5), ctx=ctx_vacio)

    assert len(res.fijos) == 0
    assert len(res.costumbre) == 0

