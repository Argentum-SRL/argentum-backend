# -*- coding: utf-8 -*-
"""
app/services/gasto_filas.py
Lecturas compartidas de filas de gasto, ciclos cerrados e IPC.
Solo lectura: sin commit, flush ni llamadas de red.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.tools import IPCCache
from app.models.transaccion import Transaccion
from app.models.usuario import Usuario
from app.services.consejos_reglas import FilaGasto
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto
from app.utils.finanzas import _indice_por_mes


def indices_ipc(db: Session) -> dict[str, Decimal]:
    """Carga los índices IPC de la base ordenados por fecha."""
    ipc_filas = db.query(IPCCache).order_by(IPCCache.fecha_dato).all()
    return _indice_por_mes(ipc_filas)


def ciclos_cerrados(
    usuario: Usuario,
    inicio_actual: date,
    min_fecha: date,
    hasta: int = 6,
) -> list[tuple[date, date]]:
    """
    Obtiene los ciclos cerrados hacia atrás a partir del ciclo actual,
    del más nuevo al más viejo, solo mientras inicio >= min_fecha.
    """
    cerrados: list[tuple[date, date]] = []
    fin_k = inicio_actual - timedelta(days=1)
    for _ in range(hasta):
        inicio_k, fin_k_calc = get_ciclo_fechas(usuario, fin_k)
        if inicio_k < min_fecha:
            break
        cerrados.append((inicio_k, fin_k_calc))
        fin_k = inicio_k - timedelta(days=1)
    return cerrados


def filas_gasto(
    db: Session,
    usuario_id: UUID,
    desde: date,
    hasta: date,
    hoy: date,
) -> list[FilaGasto]:
    """
    Consulta en una única query las filas de gasto del usuario con su categoría y subcategoría.
    """
    cond_g = condicion_gasto(
        usuario_id,
        desde=desde,
        hasta=hasta,
        moneda=None,
        hoy=hoy,
    )
    txs_query = (
        db.query(Transaccion, Categoria.nombre, Subcategoria.nombre)
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
        .filter(cond_g)
        .all()
    )

    filas: list[FilaGasto] = []
    for tx, cat_nombre, subcat_nombre in txs_query:
        moneda_tx = (
            tx.moneda.value
            if hasattr(tx.moneda, "value")
            else str(tx.moneda)
        ) if tx.moneda else "ARS"
        filas.append(
            FilaGasto(
                fecha=tx.fecha,
                monto=Decimal(str(tx.monto)),
                moneda=moneda_tx,
                categoria=cat_nombre,
                subcategoria=subcat_nombre,
                es_espejo=(tx.reintegro_de_id is not None),
                es_cuota=bool(tx.es_cuota_hija),
            )
        )

    return filas

