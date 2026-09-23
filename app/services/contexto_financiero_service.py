from decimal import Decimal
from uuid import UUID
from typing import List, Optional
from datetime import date, timedelta
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session, joinedload

from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import Transaccion
from app.services.suscripcion_service import obtener_suscripciones
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina

def _calcular_saldo_disponible_sync(
    db: Session,
    usuario_id: UUID,
    billetera_ids: Optional[List[UUID]] = None,
    wallets_override: Optional[List[Billetera]] = None
) -> dict:
    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        raise ValueError(f"Usuario {usuario_id} no encontrado")

    # 1. Saldo de Billeteras activas
    if wallets_override is not None:
        wallets = [
            w for w in wallets_override
            if getattr(w, "estado", EstadoBilletera.ACTIVA) == EstadoBilletera.ACTIVA
            and not getattr(w, "es_inversion", False)
        ]
        if billetera_ids:
            wallets = [w for w in wallets if w.id in billetera_ids]
    else:
        query_b = db.query(Billetera).filter(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.es_inversion == False,
        )
        if billetera_ids:
            query_b = query_b.filter(Billetera.id.in_(billetera_ids))
        wallets = query_b.all()

    total_billeteras_ars = Decimal("0")
    total_billeteras_usd = Decimal("0")
    for w in wallets:
        if w.moneda == Moneda.ARS:
            total_billeteras_ars += w.saldo_actual
        elif w.moneda == Moneda.USD:
            total_billeteras_usd += w.saldo_actual

    # 2. Ciclo financiero y fechas
    hoy = hoy_argentina()
    fecha_inicio_curr, fecha_fin_curr = get_ciclo_fechas(usuario, hoy)
    fecha_inicio_prox, fecha_fin_prox = get_ciclo_fechas(usuario, fecha_fin_curr + timedelta(days=1))

    # 3. Cuotas comprometidas del próximo ciclo (unpaid)
    query_c = (
        db.query(Cuota)
        .options(joinedload(Cuota.grupo))
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.usuario_id == usuario_id,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento >= fecha_inicio_prox,
            Cuota.fecha_vencimiento <= fecha_fin_prox,
        )
    )

    if billetera_ids:
        tarjeta_ids_stmt = select(TarjetaCredito.id).where(TarjetaCredito.billetera_id.in_(billetera_ids))
        parent_tx_stmt = select(Transaccion.id).where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.billetera_id.in_(billetera_ids)
        )
        query_c = query_c.filter(
            or_(
                GrupoCuotas.tarjeta_id.in_(tarjeta_ids_stmt),
                and_(
                    GrupoCuotas.tarjeta_id == None,
                    GrupoCuotas.transaccion_padre_id.in_(parent_tx_stmt)
                )
            )
        )

    cuotas = query_c.all()

    total_cuotas_ars = Decimal("0")
    total_cuotas_usd = Decimal("0")
    for c in cuotas:
        monto = c.monto_real if c.monto_real is not None else c.monto_proyectado or Decimal("0")
        if c.grupo.moneda == Moneda.ARS:
            total_cuotas_ars += monto
        elif c.grupo.moneda == Moneda.USD:
            total_cuotas_usd += monto

    # 4. Suscripciones activas
    suscripciones_activas = obtener_suscripciones(db, usuario_id, estado='activa')
    if billetera_ids:
        tarjeta_ids = [
            t_id for (t_id,) in db.query(TarjetaCredito.id).filter(
                TarjetaCredito.billetera_id.in_(billetera_ids)
            ).all()
        ]
        suscripciones_activas = [
            s for s in suscripciones_activas
            if (s.billetera_id in billetera_ids) or (s.tarjeta_id in tarjeta_ids)
        ]

    total_suscripciones_ars = Decimal("0")
    total_suscripciones_usd = Decimal("0")
    for s in suscripciones_activas:
        if s.precio_actual and s.costo_mensual_equivalente:
            monto = s.costo_mensual_equivalente
            if s.precio_actual.moneda == Moneda.ARS:
                total_suscripciones_ars += monto
            elif s.precio_actual.moneda == Moneda.USD:
                total_suscripciones_usd += monto

    # Disponible = Billeteras - Cuotas - Suscripciones
    saldo_disponible_ars = total_billeteras_ars - total_cuotas_ars - total_suscripciones_ars
    saldo_disponible_usd = total_billeteras_usd - total_cuotas_usd - total_suscripciones_usd

    return {
        "ars": {
            "total_billeteras": total_billeteras_ars,
            "cuotas_comprometidas": total_cuotas_ars,
            "suscripciones_mensuales": total_suscripciones_ars,
            "saldo_disponible": saldo_disponible_ars,
        },
        "usd": {
            "total_billeteras": total_billeteras_usd,
            "cuotas_comprometidas": total_cuotas_usd,
            "suscripciones_mensuales": total_suscripciones_usd,
            "saldo_disponible": saldo_disponible_usd,
        }
    }

async def calcular_saldo_disponible(
    db: Session,
    usuario_id: UUID,
    billetera_ids: Optional[List[UUID]] = None
) -> dict:
    return _calcular_saldo_disponible_sync(db, usuario_id, billetera_ids)


def _resumen_metas_activas_sync(db: Session, usuario_id: UUID) -> list[dict]:
    from sqlalchemy import select
    from app.models.meta import Meta, EstadoMeta
    metas = db.execute(
        select(Meta).where(Meta.usuario_id == usuario_id, Meta.estado == EstadoMeta.ACTIVA).order_by(Meta.nombre)
    ).scalars().all()
    return [
        {"nombre": m.nombre, "objetivo": float(m.monto_objetivo), "acumulado": float(m.monto_actual), "moneda": m.moneda.value}
        for m in metas
    ]


def _resumen_presupuestos_activos_sync(db: Session, usuario_id: UUID) -> list[dict]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from app.models.presupuesto import Presupuesto, EstadoPresupuesto
    from app.services import presupuesto_service
    presupuestos = db.execute(
        select(Presupuesto)
        .options(selectinload(Presupuesto.periodos))
        .where(Presupuesto.usuario_id == usuario_id, Presupuesto.estado == EstadoPresupuesto.ACTIVO)
        .order_by(Presupuesto.nombre)
    ).scalars().all()
    res = []
    for p in presupuestos:
        if getattr(p, "monto_usado_actual", None) is not None:
            usado = float(p.monto_usado_actual)
        else:
            periodo_activo = presupuesto_service.obtener_periodo_activo(None, p)
            usado = float(periodo_activo.monto_usado) if periodo_activo else 0.0
        res.append({"nombre": p.nombre, "limite": float(p.monto), "usado": usado, "moneda": p.moneda.value})
    return res
