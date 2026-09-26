"""
Servicio oficial de conciliación y cálculo de saldo teórico de billeteras.

Este módulo define la función oficial de cálculo de saldo teórico para cualquier billetera
en una fecha de corte determinada, unificando la lógica dispersa y reflejando con exactitud
cómo impactan los saldos los servicios operativos reales del backend (transaccion_service,
transferencia_service y rendimiento_billetera_service).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.utils.fecha import hoy_argentina


def calcular_saldo_teorico(
    db: Session,
    billetera_id: UUID,
    hasta: Optional[date] = None,
) -> Decimal:
    """
    Calcula el saldo teórico oficial de una billetera a una fecha de corte `hasta`.

    Fórmula matemática que refleja el impacto de los servicios reales:
        saldo_teorico = (
            saldo_inicial
            + sum(ingresos)
            - sum(egresos)
            + sum(transferencias_entrantes)
            - sum(transferencias_salientes)
            + sum(rendimientos)
        )

    Reglas de filtro y consistencia con los servicios reales:
    1. Saldo inicial:
       - Se toma billetera.saldo_inicial (o 0.00 si es nulo).
    2. Transacciones (ingresos y egresos):
       - Solo movimientos de la billetera indicada.
       - Se excluyen consumos directos o cuotas con tarjeta de crédito (metodo_pago == 'credito'),
         ya que el crédito no debita fondos de la billetera en la compra, sino vía el pago
         consolidado del resumen (que se registra como débito/egreso normal en cuenta).
       - Se excluyen transacciones con es_padre_cuotas = true (registros agrupadores) y
         es_cuota_hija = true (las cuotas de resumen ya se consolidan en el pago de tarjeta).
       - Se excluyen transacciones en estado de verificación 'pendiente', ya que no han sido
         confirmadas y por tanto transaccion_service._afecta_saldo no las impacta en saldo_actual.
       - Filtro de fecha (fecha <= hasta):
         Según la regla de negocio en transaccion_service._afecta_saldo, cuando se carga un
         movimiento con fecha futura (fecha > hoy), este no afecta el saldo_actual de la
         billetera al momento del registro. Por lo tanto, para que el saldo teórico refleje
         fielmente el saldo_actual a la fecha de corte, solo se computan transacciones con fecha <= hasta.
    3. Transferencias internas:
       - Entrantes: transferencias donde billetera_destino_id == billetera_id y fecha <= hasta.
       - Salientes: transferencias donde billetera_origen_id == billetera_id y fecha <= hasta.
    4. Rendimientos:
       - Rendimientos registrados en rendimientos_billetera para la billetera con fecha <= hasta,
         reflejando el impacto directo realizado por rendimiento_billetera_service.confirmar_rendimiento.

    Parámetros:
        db: Sesión activa de SQLAlchemy.
        billetera_id: UUID de la billetera a conciliar.
        hasta: Fecha de corte (inclusiva). Si es None, se utiliza hoy_argentina().

    Retorna:
        Decimal con el saldo teórico consolidado a la fecha de corte.
    """
    if hasta is None:
        hasta = hoy_argentina()

    # 1. Obtener billetera y su saldo inicial
    billetera = db.get(Billetera, billetera_id)
    if not billetera:
        raise ValueError(f"No se encontró la billetera con id {billetera_id}")

    saldo_inicial = billetera.saldo_inicial or Decimal("0.00")

    # 2. Transacciones confirmadas no crediticias con fecha <= hasta
    # Excluye padres de cuotas, hijas de cuotas y pendientes, replicando los filtros
    # estándar de afectación de saldo validados en transaccion_service._afecta_saldo.
    tx_row = db.execute(
        text("""
            SELECT 
                coalesce(sum(case when tipo = 'ingreso' then monto else 0 end), 0) as ingresos,
                coalesce(sum(case when tipo = 'egreso' then monto else 0 end), 0) as egresos
            FROM transacciones
            WHERE billetera_id = :bid
              AND (metodo_pago != 'credito' OR metodo_pago IS NULL)
              AND es_padre_cuotas = false
              AND es_cuota_hija = false
              AND (estado_verificacion IS NULL OR estado_verificacion != 'pendiente')
              AND fecha <= :hasta
        """),
        {"bid": billetera_id, "hasta": hasta}
    ).mappings().fetchone()

    ingresos = Decimal(str(tx_row["ingresos"])) if tx_row else Decimal("0.00")
    egresos = Decimal(str(tx_row["egresos"])) if tx_row else Decimal("0.00")

    # 3. Transferencias internas entrantes y salientes con fecha <= hasta
    tr_in = Decimal(str(
        db.execute(
            text("""
                SELECT coalesce(sum(monto_destino), 0)
                FROM transferencias_internas
                WHERE billetera_destino_id = :bid
                  AND fecha <= :hasta
            """),
            {"bid": billetera_id, "hasta": hasta}
        ).scalar() or 0
    ))

    tr_out = Decimal(str(
        db.execute(
            text("""
                SELECT coalesce(sum(monto_origen), 0)
                FROM transferencias_internas
                WHERE billetera_origen_id = :bid
                  AND fecha <= :hasta
            """),
            {"bid": billetera_id, "hasta": hasta}
        ).scalar() or 0
    ))

    # 4. Rendimientos registrados con fecha <= hasta
    # Se castea fecha a date para comparar de manera uniforme con el parámetro hasta.
    rendimientos = Decimal(str(
        db.execute(
            text("""
                SELECT coalesce(sum(monto), 0)
                FROM rendimientos_billetera
                WHERE billetera_id = :bid
                  AND cast(fecha as date) <= :hasta
            """),
            {"bid": billetera_id, "hasta": hasta}
        ).scalar() or 0
    ))

    # 5. Consolidación de saldo teórico
    saldo_teorico = saldo_inicial + ingresos - egresos + tr_in - tr_out + rendimientos
    return saldo_teorico
