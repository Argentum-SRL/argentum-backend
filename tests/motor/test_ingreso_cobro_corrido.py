"""
Tests unitarios del motor financiero: Regla de Cobro Corrido y Ventana por Fuente (fase_ingreso_cobro_corrido).
Ubicación: tests/motor/test_ingreso_cobro_corrido.py

Cubre:
T-1. Caso Usuario_09 completo (reconocimiento de sueldo con corrimiento y ventana por fuente).
T-2. Sueldo de agosto bordes de tolerancia (21/08 se corre, 20/08 queda irregular).
T-3. Atrasado de punta a punta (sueldo de marzo que entra el 02/04 se corre a marzo).
T-4. asignar_ciclos_fuente: bono con monto chico no se corre.
T-5. asignar_ciclos_fuente: quincenas no se corren.
T-6. asignar_ciclos_fuente: el vecino ya tiene su cobro, no se corre.
T-7. asignar_ciclos_fuente: bordes exactos de tolerancia en días (adelantado y atrasado).
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from app.services.ingreso_habitual_service import (
    asignar_ciclos_fuente,
    calcular_ingreso_habitual_en_memoria,
)
from tests.motor.test_ingreso_habitual import (
    MockTransaccion,
    _ctx,
    _generar_ciclos_mensuales,
)

# Los 14 ciclos de Usuario_09 tomados del punto 2 del diagnóstico
ciclos_u09 = [
    (date(2025, 9, 29), date(2025, 10, 28)),
    (date(2025, 10, 29), date(2025, 11, 27)),
    (date(2025, 11, 28), date(2025, 12, 28)),
    (date(2025, 12, 29), date(2026, 1, 28)),
    (date(2026, 1, 29), date(2026, 2, 26)),
    (date(2026, 2, 27), date(2026, 3, 26)),
    (date(2026, 3, 27), date(2026, 4, 28)),
    (date(2026, 4, 29), date(2026, 5, 28)),
    (date(2026, 5, 29), date(2026, 6, 28)),
    (date(2026, 6, 29), date(2026, 7, 28)),
    (date(2026, 7, 29), date(2026, 8, 27)),
    (date(2026, 8, 28), date(2026, 9, 28)),
    (date(2026, 9, 29), date(2026, 10, 28)),
    (date(2026, 10, 29), date(2026, 11, 26)),
]


def _crear_txs_u09(fecha_sueldo_agosto: date = date(2026, 8, 27)) -> list[MockTransaccion]:
    """Crea los 13 ingresos de Usuario_09 tomados del punto 3 del diagnóstico."""
    return [
        MockTransaccion(fecha=date(2026, 6, 1), monto="163963.84", categoria="Inversiones", subcategoria="Intereses"),
        MockTransaccion(fecha=date(2026, 6, 5), monto="200000.00", categoria="Inversiones", subcategoria="Intereses"),
        MockTransaccion(fecha=date(2026, 6, 9), monto="15073.80", categoria="Otros", subcategoria="General"),
        MockTransaccion(fecha=date(2026, 6, 10), monto="135000.00", categoria="Inversiones", subcategoria="Intereses"),
        MockTransaccion(fecha=date(2026, 6, 10), monto="330000.00", categoria="Otros", subcategoria="General"),
        MockTransaccion(fecha=date(2026, 6, 28), monto="811838.00", categoria="Otros", subcategoria="General"),
        MockTransaccion(fecha=date(2026, 6, 29), monto="3082933.31", categoria="Trabajo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 7, 30), monto="2793049.31", categoria="Trabajo", subcategoria="Sueldo"),
        MockTransaccion(fecha=fecha_sueldo_agosto, monto="2789685.00", categoria="Trabajo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 9, 21), monto="40000.00", categoria="Otros", subcategoria="General"),
        MockTransaccion(fecha=date(2026, 9, 25), monto="22000.00", categoria="Otros", subcategoria="General"),
        MockTransaccion(fecha=date(2026, 9, 29), monto="3056428.52", categoria="Trabajo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 10, 29), monto="3005568.97", categoria="Trabajo", subcategoria="Sueldo"),
    ]


def test_t1_caso_usuario_09_completo():
    """T-1. Caso Usuario_09 completo: reconoce ingreso regular de Trabajo / Sueldo."""
    hoy = date(2026, 11, 5)
    ctx = _ctx(hoy)
    txs = _crear_txs_u09()

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos_u09, hoy, ctx, moneda="ARS")
    assert res.tipo == "regular"
    assert res.monto == Decimal("3005568.97")
    assert res.motivo_sin_datos is None
    assert len(res.fuentes) == 1
    f = res.fuentes[0]
    assert f.categoria_nombre == "Trabajo"
    assert f.subcategoria_nombre == "Sueldo"
    assert f.ciclos_presentes == 4


def test_t2_sueldo_agosto_bordes_tolerancia():
    """T-2. Sueldo de agosto: 21/08 (7 días antes) se corre; 20/08 (8 días antes) queda irregular."""
    hoy = date(2026, 11, 5)
    ctx = _ctx(hoy)

    # Con sueldo el 21/08 (7 días antes de inicio de ciclo 12: 28/08)
    txs_21 = _crear_txs_u09(fecha_sueldo_agosto=date(2026, 8, 21))
    res_21 = calcular_ingreso_habitual_en_memoria(txs_21, ciclos_u09, hoy, ctx, moneda="ARS")
    assert res_21.tipo == "regular"
    assert res_21.monto == Decimal("3005568.97")
    assert res_21.motivo_sin_datos is None
    assert len(res_21.fuentes) == 1
    assert res_21.fuentes[0].ciclos_presentes == 4

    # Con sueldo el 20/08 (8 días antes)
    txs_20 = _crear_txs_u09(fecha_sueldo_agosto=date(2026, 8, 20))
    res_20 = calcular_ingreso_habitual_en_memoria(txs_20, ciclos_u09, hoy, ctx, moneda="ARS")
    assert res_20.tipo == "sin_datos"
    assert res_20.motivo_sin_datos == "irregular"
    assert res_20.monto is None


def test_t3_atrasado_punta_a_punta():
    """T-3. Atrasado de punta a punta: cobro de marzo entra el 02/04 y se corre a marzo."""
    ciclos_completos = _generar_ciclos_mensuales(2026, 2, 5)  # feb, mar, abr, may, jun
    ciclo_julio = (date(2026, 7, 1), date(2026, 7, 31))
    ciclos = ciclos_completos + [ciclo_julio]
    hoy = date(2026, 7, 10)
    ctx = _ctx(hoy)

    txs = [
        MockTransaccion(fecha=date(2026, 2, 28), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 4, 2), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 4, 28), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 5, 28), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 6, 28), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]

    res = calcular_ingreso_habitual_en_memoria(txs, ciclos, hoy, ctx, moneda="ARS")
    assert res.tipo == "regular"
    assert res.monto == Decimal("1000000.00")
    assert res.motivo_sin_datos is None
    assert len(res.fuentes) == 1
    assert res.fuentes[0].ciclos_presentes == 5


def test_t4_bono_que_no_se_corre():
    """T-4. asignar_ciclos_fuente: cobro menor (200.000 vs 1.000.000) no se corre por no cumplir parecido."""
    ciclos_completos = _generar_ciclos_mensuales(2026, 1, 6)
    ciclo_julio = (date(2026, 7, 1), date(2026, 7, 31))
    ciclos = ciclos_completos + [ciclo_julio]

    txs = [
        MockTransaccion(fecha=date(2026, m, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo")
        for m in range(1, 7)
    ]
    tx_bono = MockTransaccion(fecha=date(2026, 6, 27), monto=200000, categoria="Empleo", subcategoria="Sueldo")
    txs.append(tx_bono)

    asig = asignar_ciclos_fuente(txs, ciclos)
    # El cobro del 27/06 queda en el índice de junio (5)
    assert asig[str(tx_bono.id)] == 5


def test_t5_quincenas_que_no_se_corren():
    """T-5. asignar_ciclos_fuente: fuente con 2 cobros por ciclo (quincenas) no se corre."""
    ciclos_completos = _generar_ciclos_mensuales(2026, 1, 6)
    ciclo_julio = (date(2026, 7, 1), date(2026, 7, 31))
    ciclos = ciclos_completos + [ciclo_julio]

    txs = []
    esperado_por_tx = {}
    for idx in range(6):
        m = idx + 1
        ult_dia = calendar.monthrange(2026, m)[1]
        tx1 = MockTransaccion(fecha=date(2026, m, 15), monto=500000, categoria="Empleo", subcategoria="Sueldo")
        tx2 = MockTransaccion(fecha=date(2026, m, ult_dia), monto=500000, categoria="Empleo", subcategoria="Sueldo")
        txs.extend([tx1, tx2])
        esperado_por_tx[str(tx1.id)] = idx
        esperado_por_tx[str(tx2.id)] = idx

    asig = asignar_ciclos_fuente(txs, ciclos)
    for tx_id, idx_esp in esperado_por_tx.items():
        assert asig[tx_id] == idx_esp


def test_t6_el_vecino_ya_tiene_su_cobro():
    """T-6. asignar_ciclos_fuente: no se corre si el ciclo vecino ya tiene cobro."""
    ciclos_completos = _generar_ciclos_mensuales(2026, 1, 6)
    ciclo_julio = (date(2026, 7, 1), date(2026, 7, 31))
    ciclos = ciclos_completos + [ciclo_julio]

    txs = [
        MockTransaccion(fecha=date(2026, m, 5), monto=1000000, categoria="Empleo", subcategoria="Sueldo")
        for m in range(1, 7)
    ]
    tx_extra = MockTransaccion(fecha=date(2026, 3, 28), monto=950000, categoria="Empleo", subcategoria="Sueldo")
    txs.append(tx_extra)

    asig = asignar_ciclos_fuente(txs, ciclos)
    # Queda en marzo (índice 2) porque abril ya tiene cobro
    assert asig[str(tx_extra.id)] == 2


def test_t7_bordes_tolerancia_dias():
    """T-7. asignar_ciclos_fuente: bordes exactos de tolerancia_dias (adelantado y atrasado)."""
    ciclos_completos = _generar_ciclos_mensuales(2026, 1, 5)  # ene, feb, mar, abr, may
    ciclo_junio = (date(2026, 6, 1), date(2026, 6, 30))
    ciclos = ciclos_completos + [ciclo_junio]

    # 1. Adelantado: marzo (k=2), abril (k1=3). inicio(abril) = 2026-04-01
    # inicio - 7 días = 2026-03-25 se corre a abril (índice 3)
    txs_ad7 = [
        MockTransaccion(fecha=date(2026, 1, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 2, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 3, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 3, 25), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 5, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    asig_ad7 = asignar_ciclos_fuente(txs_ad7, ciclos)
    assert asig_ad7[str(txs_ad7[3].id)] == 3

    # inicio - 8 días = 2026-03-24 no se corre (queda en marzo, índice 2)
    txs_ad8 = [
        MockTransaccion(fecha=date(2026, 1, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 2, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 3, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 3, 24), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 5, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    asig_ad8 = asignar_ciclos_fuente(txs_ad8, ciclos)
    assert asig_ad8[str(txs_ad8[3].id)] == 2

    # 2. Atrasado: marzo (k=2) sin cobros, abril (k1=3) con 2 cobros.
    # inicio + 6 días = 2026-04-07 se corre a marzo (índice 2)
    txs_at6 = [
        MockTransaccion(fecha=date(2026, 1, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 2, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 4, 7), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 4, 28), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 5, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    asig_at6 = asignar_ciclos_fuente(txs_at6, ciclos)
    assert asig_at6[str(txs_at6[2].id)] == 2

    # inicio + 7 días = 2026-04-08 no se corre (queda en abril, índice 3)
    txs_at7 = [
        MockTransaccion(fecha=date(2026, 1, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 2, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 4, 8), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 4, 28), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
        MockTransaccion(fecha=date(2026, 5, 10), monto=1000000, categoria="Empleo", subcategoria="Sueldo"),
    ]
    asig_at7 = asignar_ciclos_fuente(txs_at7, ciclos)
    assert asig_at7[str(txs_at7[2].id)] == 3

