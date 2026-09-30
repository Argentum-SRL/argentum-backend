"""
Servicio de carga y ciclos base del motor financiero de Argentum.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.suscripcion import EstadoSuscripcion, Suscripcion
from app.models.tools import IPCCache
from app.models.transaccion import Transaccion
from app.models.usuario import Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import cargar_contexto
from app.utils.fecha import hoy_argentina
from app.utils.finanzas import _indice_por_mes


def ciclos_anteriores(usuario: Usuario, hoy: date, cantidad: int = 12) -> list[tuple[date, date]]:
    inicio_actual, _ = get_ciclo_fechas(usuario, hoy)
    result = []
    cursor = inicio_actual - timedelta(days=1)
    for _ in range(cantidad):
        inicio, fin = get_ciclo_fechas(usuario, cursor)
        result.append((inicio, fin))
        cursor = inicio - timedelta(days=1)
    return list(reversed(result))



def cargar_datos_motor(db: Session, usuario: Usuario, fecha_referencia: date | None = None) -> dict[str, Any]:
    hoy = fecha_referencia or hoy_argentina()
    txs = db.execute(
        select(Transaccion)
        .options(
            joinedload(Transaccion.categoria),
            joinedload(Transaccion.subcategoria),
            joinedload(Transaccion.billetera),
        )
        .where(Transaccion.usuario_id == usuario.id, Transaccion.fecha <= hoy)
        .order_by(Transaccion.fecha)
    ).scalars().all()
    ipc_records = db.execute(select(IPCCache).order_by(IPCCache.fecha_dato)).scalars().all()
    ipc = _indice_por_mes(ipc_records)
    cuotas = db.execute(
        select(Cuota, GrupoCuotas)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .options(joinedload(GrupoCuotas.transaccion_padre))
        .where(GrupoCuotas.usuario_id == usuario.id)
    ).all()
    suscripciones = db.execute(
        select(Suscripcion).where(Suscripcion.usuario_id == usuario.id, Suscripcion.estado == EstadoSuscripcion.ACTIVA)
    ).scalars().all()
    historial_subs = db.execute(
        select(HistorialSuscripcion).join(Suscripcion).where(Suscripcion.usuario_id == usuario.id)
    ).scalars().all()
    ctx = cargar_contexto(db, usuario.id, hoy)
    return {"hoy": hoy, "txs": txs, "ipc": ipc, "cuotas": cuotas, "suscripciones": suscripciones, "historial_subs": historial_subs, "ctx": ctx}

