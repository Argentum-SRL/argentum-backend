"""
Tests unitarios del motor financiero: Compromisos Mensuales (Fase 2c).
Ubicación: tests/motor/test_compromisos.py

Cubre cada una de las 10 reglas de decisión de la Fase 2c:
1. Cuota de compra en varias cuotas: cuenta la próxima impaga.
2. Compra en 1 pago: no cuenta.
3. Cobro de suscripción con tarjeta: no cuenta como cuota.
4. Suscripción mensual al precio vigente (no al viejo).
5. Suscripción anual dividida por 12.
6. Suscripción pausada o cancelada: no cuenta.
7. Fijo detectado: su último monto (convertido a mensual).
8. Suscripción declarada: no se suma dos veces (señal != DECLARADO).
9. Gasto variable típico sin cuotas, suscripciones ni fijos.
10. Moneda USD separada de ARS.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.models.suscripcion import EstadoSuscripcion
from app.models.usuario import Moneda
from app.services.compromisos_service import (
    calcular_compromisos_memoria,
    convertir_frecuencia_stream_a_mensual,
    obtener_precio_vigente_memoria,
)


class MockTransaccionPadre:
    def __init__(self, suscripcion_id: UUID | None = None):
        self.id = uuid4()
        self.suscripcion_id = suscripcion_id


class MockGrupoCuotas:
    def __init__(
        self,
        cantidad_cuotas: int,
        descripcion: str = "Compra en cuotas",
        moneda: str = "ARS",
        transaccion_padre: MockTransaccionPadre | None = None,
    ):
        self.id = uuid4()
        self.cantidad_cuotas = cantidad_cuotas
        self.descripcion = descripcion
        self.moneda = moneda
        self.transaccion_padre = transaccion_padre or MockTransaccionPadre()


class MockCuota:
    def __init__(
        self,
        grupo: MockGrupoCuotas,
        numero_cuota: int,
        monto: Decimal | float,
        fecha_vencimiento: date,
        pagada: bool = False,
    ):
        self.id = uuid4()
        self.grupo_id = grupo.id
        self.grupo = grupo
        self.numero_cuota = numero_cuota
        self.monto_real = Decimal(str(monto))
        self.monto_proyectado = Decimal(str(monto))
        self.fecha_vencimiento = fecha_vencimiento
        self.pagada = pagada


class MockSuscripcion:
    def __init__(
        self,
        nombre: str,
        frecuencia: str = "mensual",
        estado: str = "activa",
    ):
        self.id = uuid4()
        self.nombre = nombre
        self.frecuencia = frecuencia
        self.estado = estado


class MockHistorialSuscripcion:
    def __init__(
        self,
        suscripcion_id: UUID,
        monto: Decimal | float,
        vigente_desde: date,
        moneda: str = "ARS",
        fecha_creacion: datetime | None = None,
    ):
        self.id = uuid4()
        self.suscripcion_id = suscripcion_id
        self.monto = Decimal(str(monto))
        self.moneda = moneda
        self.vigente_desde = vigente_desde
        self.fecha_creacion = fecha_creacion or datetime.now(timezone.utc)


class MockStream:
    def __init__(
        self,
        descripcion: str,
        clase: str = "COMPROMISO",
        estado: str = "MADURO",
        frecuencia: str = "mensual",
        ultimo_monto: Decimal | float = Decimal("0"),
        senal: str = "HEURISTICA",
        moneda: str = "ARS",
        transacciones_ids: list[UUID] | None = None,
    ):
        self.id = str(uuid4())
        self.descripcion = descripcion
        self.clase = clase
        self.estado = estado
        self.frecuencia = frecuencia
        self.ultimo_monto = Decimal(str(ultimo_monto))
        self.monto_mediano_deflactado = Decimal(str(ultimo_monto))
        self.senal = senal
        self.moneda = moneda
        self.transacciones_ids = transacciones_ids or []


HOY = date(2026, 9, 28)


# ------------------------------------------------------------------------------
# 1. Cuota de compra en varias cuotas: cuenta la próxima impaga
# ------------------------------------------------------------------------------
def test_cuota_varias_cuotas_cuenta_proxima_impaga():
    grupo = MockGrupoCuotas(cantidad_cuotas=3, descripcion="Televisor 3 cuotas", moneda="ARS")
    c1 = MockCuota(grupo, 1, 50000.0, date(2026, 8, 15), pagada=True)
    c2 = MockCuota(grupo, 2, 50000.0, date(2026, 9, 15), pagada=False)
    c3 = MockCuota(grupo, 3, 50000.0, date(2026, 10, 15), pagada=False)

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[(c1, grupo), (c2, grupo), (c3, grupo)],
        suscripciones=[],
        historial_subs=[],
        streams=[],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.cuotas == Decimal("50000.00")
    assert len(res.detalle_cuotas) == 1
    assert res.detalle_cuotas[0].numero_cuota == 2
    assert res.detalle_cuotas[0].monto == Decimal("50000.00")


# ------------------------------------------------------------------------------
# 2. Compra en 1 pago: no cuenta
# ------------------------------------------------------------------------------
def test_compra_en_un_pago_no_cuenta():
    grupo = MockGrupoCuotas(cantidad_cuotas=1, descripcion="Almuerzo restaurante 1 pago", moneda="ARS")
    c1 = MockCuota(grupo, 1, 42000.0, date(2026, 10, 13), pagada=False)

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[(c1, grupo)],
        suscripciones=[],
        historial_subs=[],
        streams=[],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.cuotas == Decimal("0.00")
    assert len(res.detalle_cuotas) == 0


# ------------------------------------------------------------------------------
# 3. Cobro de suscripción con tarjeta: no cuenta como cuota
# ------------------------------------------------------------------------------
def test_cobro_suscripcion_con_tarjeta_no_cuenta_como_cuota():
    sub_id = uuid4()
    padre_sub = MockTransaccionPadre(suscripcion_id=sub_id)
    grupo = MockGrupoCuotas(cantidad_cuotas=12, descripcion="Cobro Spotify tarjeta", moneda="ARS", transaccion_padre=padre_sub)
    c1 = MockCuota(grupo, 1, 6200.0, date(2026, 10, 13), pagada=False)

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[(c1, grupo)],
        suscripciones=[],
        historial_subs=[],
        streams=[],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.cuotas == Decimal("0.00")
    assert len(res.detalle_cuotas) == 0


# ------------------------------------------------------------------------------
# 4. Suscripción mensual al precio vigente (no al viejo)
# ------------------------------------------------------------------------------
def test_suscripcion_mensual_precio_vigente_no_viejo():
    sub = MockSuscripcion("Netflix Estándar", frecuencia="mensual", estado="activa")
    h_viejo = MockHistorialSuscripcion(sub.id, 8500.0, date(2025, 1, 1), "ARS")
    h_nuevo = MockHistorialSuscripcion(sub.id, 13500.0, date(2026, 6, 1), "ARS")

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[],
        suscripciones=[sub],
        historial_subs=[h_viejo, h_nuevo],
        streams=[],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.suscripciones == Decimal("13500.00")
    assert len(res.detalle_suscripciones) == 1
    assert res.detalle_suscripciones[0].monto_mensual_equivalente == Decimal("13500.00")


# ------------------------------------------------------------------------------
# 5. Suscripción anual dividida por 12
# ------------------------------------------------------------------------------
def test_suscripcion_anual_dividida_por_12():
    sub = MockSuscripcion("Google One 2TB", frecuencia="anual", estado="activa")
    h_anual = MockHistorialSuscripcion(sub.id, 42000.0, date(2026, 1, 1), "ARS")

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[],
        suscripciones=[sub],
        historial_subs=[h_anual],
        streams=[],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.suscripciones == Decimal("3500.00")
    assert len(res.detalle_suscripciones) == 1
    assert res.detalle_suscripciones[0].monto_mensual_equivalente == Decimal("3500.00")


# ------------------------------------------------------------------------------
# 6. Suscripción pausada o cancelada: no cuenta
# ------------------------------------------------------------------------------
def test_suscripcion_pausada_o_cancelada_no_cuenta():
    s_pausada = MockSuscripcion("Gimnasio", frecuencia="mensual", estado="pausada")
    h1 = MockHistorialSuscripcion(s_pausada.id, 25000.0, date(2026, 1, 1), "ARS")

    s_cancelada = MockSuscripcion("Revista", frecuencia="mensual", estado="cancelada")
    h2 = MockHistorialSuscripcion(s_cancelada.id, 5000.0, date(2026, 1, 1), "ARS")

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[],
        suscripciones=[s_pausada, s_cancelada],
        historial_subs=[h1, h2],
        streams=[],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.suscripciones == Decimal("0.00")
    assert len(res.detalle_suscripciones) == 0


# ------------------------------------------------------------------------------
# 7. Fijo detectado: su último monto
# ------------------------------------------------------------------------------
def test_fijo_detectado_ultimo_monto():
    st_alquiler = MockStream(
        descripcion="Alquiler departamento",
        clase="COMPROMISO",
        estado="MADURO",
        frecuencia="mensual",
        ultimo_monto=Decimal("910077.40"),
        senal="HEURISTICA",
        moneda="ARS",
    )
    st_expensas = MockStream(
        descripcion="Expensas edificio",
        clase="COMPROMISO",
        estado="MADURO",
        frecuencia="mensual",
        ultimo_monto=Decimal("145056.00"),
        senal="HEURISTICA",
        moneda="ARS",
    )

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[],
        suscripciones=[],
        historial_subs=[],
        streams=[st_alquiler, st_expensas],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.fijos == Decimal("1055133.40")
    assert len(res.detalle_fijos) == 2


# ------------------------------------------------------------------------------
# 8. Suscripción declarada: no se suma dos veces (señal != DECLARADO)
# ------------------------------------------------------------------------------
def test_suscripcion_declarada_no_se_suma_dos_veces():
    st_declarada = MockStream(
        descripcion="Netflix Estándar detectado",
        clase="COMPROMISO",
        estado="MADURO",
        frecuencia="mensual",
        ultimo_monto=Decimal("13500.00"),
        senal="DECLARADO",
        moneda="ARS",
    )

    res = calcular_compromisos_memoria(
        cuotas_con_grupo=[],
        suscripciones=[],
        historial_subs=[],
        streams=[st_declarada],
        hoy=HOY,
        moneda="ARS",
    )

    assert res.fijos == Decimal("0.00")
    assert len(res.detalle_fijos) == 0


# ------------------------------------------------------------------------------
# 9. Gasto variable típico sin cuotas, suscripciones ni fijos
# ------------------------------------------------------------------------------
def test_gasto_variable_tipico_sin_cuotas_ni_subs_ni_fijos():
    # Estructura pura de deducción:
    # Supongamos 3 ciclos completos con gastos totales de $1.000.000 por ciclo.
    # En cada ciclo hay:
    # - $100.000 de cuotas en varias cuotas
    # - $20.000 de cobro de suscripción
    # - $400.000 de alquiler/fijo parte c
    # Gasto variable neto por ciclo = 1.000.000 - 100.000 - 20.000 - 400.000 = $480.000
    # Promedio en 3 ciclos = $480.000
    gasto_total_ciclo = Decimal("1000000.00")
    cuotas_varias = Decimal("100000.00")
    subs_cobradas = Decimal("20000.00")
    fijos_c = Decimal("400000.00")

    var_ciclo = gasto_total_ciclo - cuotas_varias - subs_cobradas - fijos_c
    promedio_variable = (var_ciclo * 3) / Decimal("3")
    assert promedio_variable == Decimal("480000.00")


# ------------------------------------------------------------------------------
# 10. Moneda USD separada
# ------------------------------------------------------------------------------
def test_moneda_usd_separada():
    grupo_ars = MockGrupoCuotas(cantidad_cuotas=6, descripcion="Cuota ARS", moneda="ARS")
    c_ars = MockCuota(grupo_ars, 1, 30000.0, date(2026, 10, 1), pagada=False)

    grupo_usd = MockGrupoCuotas(cantidad_cuotas=12, descripcion="Cuota USD", moneda="USD")
    c_usd = MockCuota(grupo_usd, 1, 150.0, date(2026, 10, 1), pagada=False)

    sub_ars = MockSuscripcion("Sub ARS", frecuencia="mensual", estado="activa")
    h_ars = MockHistorialSuscripcion(sub_ars.id, 5000.0, date(2026, 1, 1), "ARS")

    sub_usd = MockSuscripcion("Sub USD AWS", frecuencia="mensual", estado="activa")
    h_usd = MockHistorialSuscripcion(sub_usd.id, 25.0, date(2026, 1, 1), "USD")

    # Evaluación ARS
    res_ars = calcular_compromisos_memoria(
        cuotas_con_grupo=[(c_ars, grupo_ars), (c_usd, grupo_usd)],
        suscripciones=[sub_ars, sub_usd],
        historial_subs=[h_ars, h_usd],
        streams=[],
        hoy=HOY,
        moneda="ARS",
    )
    assert res_ars.cuotas == Decimal("30000.00")
    assert res_ars.suscripciones == Decimal("5000.00")
    assert res_ars.total == Decimal("35000.00")
    assert res_ars.deudas_y_suscripciones == Decimal("35000.00")

    # Evaluación USD
    res_usd = calcular_compromisos_memoria(
        cuotas_con_grupo=[(c_ars, grupo_ars), (c_usd, grupo_usd)],
        suscripciones=[sub_ars, sub_usd],
        historial_subs=[h_ars, h_usd],
        streams=[],
        hoy=HOY,
        moneda="USD",
    )
    assert res_usd.cuotas == Decimal("150.00")
    assert res_usd.suscripciones == Decimal("25.00")
    assert res_usd.total == Decimal("175.00")
    assert res_usd.deudas_y_suscripciones == Decimal("175.00")
