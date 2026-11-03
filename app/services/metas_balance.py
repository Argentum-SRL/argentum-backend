# -*- coding: utf-8 -*-
"""
app/services/metas_balance.py

Módulo de cálculo y formateo de la línea de metas debajo del balance financiero.
El balance sigue siendo ingresos − gastos (los aportes no son gastos); aquí se
calcula y formatea el texto informativo de lo apartado o retirado de metas en el período.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.usuario import Moneda
from app.utils.formato import formatear_monto


def linea_metas(balance: Decimal | float, neto: Decimal | float, moneda: str | Moneda) -> str | None:
    """
    Genera el texto informativo para la línea de metas debajo del balance:
    - neto == 0 -> None
    - neto > 0 y balance > 0 y neto <= balance -> f"De eso, apartaste {m(neto)} para tus metas."
    - neto > 0 en otro caso -> f"Apartaste {m(neto)} para tus metas."
    - neto < 0 -> f"Sacaste {m(abs(neto))} de tus metas."
    con m(x) = formatear_monto(x, moneda, con_decimales=False).
    """
    neto_dec = Decimal(str(neto))
    balance_dec = Decimal(str(balance))

    if neto_dec == 0:
        return None

    m = formatear_monto(abs(neto_dec), moneda, con_decimales=False)

    if neto_dec > 0:
        if balance_dec > 0 and neto_dec <= balance_dec:
            return f"De eso, apartaste {m} para tus metas."
        return f"Apartaste {m} para tus metas."
    else:
        return f"Sacaste {m} de tus metas."


def aportes_netos_metas(
    db: Session,
    usuario_id: UUID,
    desde: date,
    hasta: date,
    moneda: str | Moneda,
    hoy: date,
    billetera_ids: Optional[List[UUID]] = None,
) -> Decimal:
    """
    Calcula la suma de los egresos con movimiento_meta_id no nulo menos la suma de los ingresos
    con movimiento_meta_id no nulo, en esa moneda, con fecha entre desde y hasta y fecha <= hoy
    (y en esas billeteras si vienen). Realiza una sola consulta SQL.
    """
    mon_val = moneda.value if hasattr(moneda, "value") else str(moneda)

    stmt = select(
        func.coalesce(
            func.sum(
                case(
                    (Transaccion.tipo == TipoTransaccion.EGRESO, Transaccion.monto),
                    (Transaccion.tipo == TipoTransaccion.INGRESO, -Transaccion.monto),
                    else_=Decimal("0.00"),
                )
            ),
            Decimal("0.00"),
        )
    ).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.movimiento_meta_id.isnot(None),
        Transaccion.moneda == mon_val,
        Transaccion.fecha >= desde,
        Transaccion.fecha <= hasta,
        Transaccion.fecha <= hoy,
    )

    if billetera_ids is not None:
        stmt = stmt.where(Transaccion.billetera_id.in_(billetera_ids))

    res = db.execute(stmt).scalar()
    return Decimal(str(res or "0.00"))

