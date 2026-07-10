from decimal import Decimal
from uuid import UUID
from typing import List, Optional
from datetime import date, datetime, timezone, timedelta
from sqlalchemy import select, func, or_, and_
from sqlalchemy.orm import Session

from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import Transaccion
from app.services.suscripcion_service import obtener_suscripciones
from app.services.dashboard_service import get_ciclo_fechas
from app.services.perfil_financiero_service import obtener_cotizacion_dolar

def _calcular_saldo_disponible_sync(
    db: Session,
    usuario_id: UUID,
    target_moneda: Moneda,
    billetera_ids: Optional[List[UUID]] = None
) -> dict:
    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        raise ValueError(f"Usuario {usuario_id} no encontrado")

    if isinstance(target_moneda, str):
        target_moneda = Moneda(target_moneda.upper())

    cotizacion = obtener_cotizacion_dolar(usuario)

    # 1. Saldo de Billeteras activas
    query_b = db.query(Billetera).filter(
        Billetera.usuario_id == usuario_id,
        Billetera.estado == EstadoBilletera.ACTIVA
    )
    if billetera_ids:
        query_b = query_b.filter(Billetera.id.in_(billetera_ids))
    wallets = query_b.all()

    total_billeteras = Decimal("0")
    for w in wallets:
        monto = w.saldo_actual
        if w.moneda != target_moneda:
            if target_moneda == Moneda.ARS:
                monto = w.saldo_actual * cotizacion
            else:
                monto = w.saldo_actual / cotizacion
        total_billeteras += monto

    # 2. Ciclo financiero y fechas
    hoy = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    fecha_inicio_curr, fecha_fin_curr = get_ciclo_fechas(usuario, hoy)
    fecha_inicio_prox, fecha_fin_prox = get_ciclo_fechas(usuario, fecha_fin_curr + timedelta(days=1))

    # 3. Cuotas comprometidas del próximo ciclo (unpaid)
    query_c = db.query(Cuota).join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id).filter(
        GrupoCuotas.usuario_id == usuario_id,
        Cuota.pagada == False,
        Cuota.fecha_vencimiento >= fecha_inicio_prox,
        Cuota.fecha_vencimiento <= fecha_fin_prox
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

    total_cuotas = Decimal("0")
    for c in cuotas:
        monto = c.monto_real if c.monto_real is not None else c.monto_proyectado or Decimal("0")
        if c.grupo.moneda != target_moneda:
            if target_moneda == Moneda.ARS:
                monto = monto * cotizacion
            else:
                monto = monto / cotizacion
        total_cuotas += monto

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

    total_suscripciones = Decimal("0")
    for s in suscripciones_activas:
        if s.precio_actual and s.costo_mensual_equivalente:
            monto = s.costo_mensual_equivalente
            if s.precio_actual.moneda != target_moneda:
                if target_moneda == Moneda.ARS:
                    monto = monto * cotizacion
                else:
                    monto = monto / cotizacion
            total_suscripciones += monto

    # Disponible = Billeteras - Cuotas - Suscripciones
    saldo_disponible = total_billeteras - total_cuotas - total_suscripciones

    return {
        "total_billeteras": total_billeteras,
        "cuotas_comprometidas": total_cuotas,
        "suscripciones_mensuales": total_suscripciones,
        "saldo_disponible": saldo_disponible
    }

async def calcular_saldo_disponible(
    db: Session,
    usuario_id: UUID,
    target_moneda: Moneda,
    billetera_ids: Optional[List[UUID]] = None
) -> dict:
    return _calcular_saldo_disponible_sync(db, usuario_id, target_moneda, billetera_ids)
