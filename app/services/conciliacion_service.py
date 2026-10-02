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

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import EstadoVerificacionTransaccion, MetodoPago, TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
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
            + sum(ajustes)
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
    5. Ajustes de saldo:
       - Ajustes registrados en ajustes_saldo para la billetera con fecha <= hasta,
         reflejando el impacto directo realizado por ajuste_saldo_service.registrar_control.

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

    # 5. Ajustes de saldo registrados con fecha <= hasta
    ajustes = Decimal(str(
        db.execute(
            text("""
                SELECT coalesce(sum(monto), 0)
                FROM ajustes_saldo
                WHERE billetera_id = :bid
                  AND fecha <= :hasta
            """),
            {"bid": billetera_id, "hasta": hasta}
        ).scalar() or 0
    ))

    # 6. Consolidación de saldo teórico
    saldo_teorico = saldo_inicial + ingresos - egresos + tr_in - tr_out + rendimientos + ajustes
    return saldo_teorico


def movimientos_por_dia(
    db: Session,
    billetera_id: UUID,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
) -> dict[date, dict[str, Decimal]]:
    """
    Calcula los movimientos acumulados (entradas y salidas) agrupados por día para una billetera.
    Escrita con select de SQLAlchemy (nada de text).

    - salidas = egresos (mismos filtros de transacciones que calcular_saldo_teorico) + transferencias salientes (monto_origen)
    - entradas = ingresos + transferencias entrantes (monto_destino) + rendimientos (func.date(fecha))
    - no incluye ajustes.
    """
    res: dict[date, dict[str, Decimal]] = {}

    # 1. Transacciones (ingresos y egresos)
    stmt_tx = (
        select(
            Transaccion.fecha,
            Transaccion.tipo,
            func.coalesce(func.sum(Transaccion.monto), Decimal("0.00")).label("total"),
        )
        .where(
            Transaccion.billetera_id == billetera_id,
            (Transaccion.metodo_pago != MetodoPago.CREDITO) | (Transaccion.metodo_pago.is_(None)),
            Transaccion.es_padre_cuotas.is_(False),
            Transaccion.es_cuota_hija.is_(False),
            (Transaccion.estado_verificacion.is_(None)) | (Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE),
        )
    )
    if desde is not None:
        stmt_tx = stmt_tx.where(Transaccion.fecha >= desde)
    if hasta is not None:
        stmt_tx = stmt_tx.where(Transaccion.fecha <= hasta)
    stmt_tx = stmt_tx.group_by(Transaccion.fecha, Transaccion.tipo)

    for row in db.execute(stmt_tx).all():
        f_dia, f_tipo, total = row[0], row[1], Decimal(str(row[2]))
        d_obj = f_dia if isinstance(f_dia, date) else date.fromisoformat(str(f_dia))
        entry = res.setdefault(d_obj, {"salidas": Decimal("0.00"), "entradas": Decimal("0.00")})
        if f_tipo == TipoTransaccion.EGRESO or str(f_tipo).lower() == "egreso":
            entry["salidas"] += total
        elif f_tipo == TipoTransaccion.INGRESO or str(f_tipo).lower() == "ingreso":
            entry["entradas"] += total

    # 2. Transferencias salientes (monto_origen)
    stmt_tr_out = (
        select(
            TransferenciaInterna.fecha,
            func.coalesce(func.sum(TransferenciaInterna.monto_origen), Decimal("0.00")).label("total"),
        )
        .where(TransferenciaInterna.billetera_origen_id == billetera_id)
    )
    if desde is not None:
        stmt_tr_out = stmt_tr_out.where(TransferenciaInterna.fecha >= desde)
    if hasta is not None:
        stmt_tr_out = stmt_tr_out.where(TransferenciaInterna.fecha <= hasta)
    stmt_tr_out = stmt_tr_out.group_by(TransferenciaInterna.fecha)

    for row in db.execute(stmt_tr_out).all():
        f_dia, total = row[0], Decimal(str(row[1]))
        d_obj = f_dia if isinstance(f_dia, date) else date.fromisoformat(str(f_dia))
        res.setdefault(d_obj, {"salidas": Decimal("0.00"), "entradas": Decimal("0.00")})["salidas"] += total

    # 3. Transferencias entrantes (monto_destino)
    stmt_tr_in = (
        select(
            TransferenciaInterna.fecha,
            func.coalesce(func.sum(TransferenciaInterna.monto_destino), Decimal("0.00")).label("total"),
        )
        .where(TransferenciaInterna.billetera_destino_id == billetera_id)
    )
    if desde is not None:
        stmt_tr_in = stmt_tr_in.where(TransferenciaInterna.fecha >= desde)
    if hasta is not None:
        stmt_tr_in = stmt_tr_in.where(TransferenciaInterna.fecha <= hasta)
    stmt_tr_in = stmt_tr_in.group_by(TransferenciaInterna.fecha)

    for row in db.execute(stmt_tr_in).all():
        f_dia, total = row[0], Decimal(str(row[1]))
        d_obj = f_dia if isinstance(f_dia, date) else date.fromisoformat(str(f_dia))
        res.setdefault(d_obj, {"salidas": Decimal("0.00"), "entradas": Decimal("0.00")})["entradas"] += total

    # 4. Rendimientos (func.date(fecha))
    stmt_rend = (
        select(
            func.date(RendimientoBilletera.fecha).label("dia"),
            func.coalesce(func.sum(RendimientoBilletera.monto), Decimal("0.00")).label("total"),
        )
        .where(RendimientoBilletera.billetera_id == billetera_id)
    )
    if desde is not None:
        stmt_rend = stmt_rend.where(func.date(RendimientoBilletera.fecha) >= desde)
    if hasta is not None:
        stmt_rend = stmt_rend.where(func.date(RendimientoBilletera.fecha) <= hasta)
    stmt_rend = stmt_rend.group_by(func.date(RendimientoBilletera.fecha))

    for row in db.execute(stmt_rend).all():
        f_dia, total = row[0], Decimal(str(row[1]))
        if f_dia is not None:
            d_obj = f_dia if isinstance(f_dia, date) else date.fromisoformat(str(f_dia))
            res.setdefault(d_obj, {"salidas": Decimal("0.00"), "entradas": Decimal("0.00")})["entradas"] += total

    return res
