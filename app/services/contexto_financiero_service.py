from decimal import Decimal
from uuid import UUID
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.services.dashboard_service import get_ciclo_fechas
from app.services.pagos_proximos_service import calcular_disponible_libre
from app.utils.fecha import hoy_argentina


def _calcular_saldo_disponible_sync(
    db: Session,
    usuario_id: UUID,
    billetera_ids: Optional[List[UUID]] = None,
    wallets_override: Optional[List[Billetera]] = None,
    total_billeteras_override: Optional[Dict[str, Decimal]] = None,
) -> dict:
    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        raise ValueError(f"Usuario {usuario_id} no encontrado")

    hoy = hoy_argentina()
    _fecha_inicio_curr, fecha_fin_curr = get_ciclo_fechas(usuario, hoy)

    # Si se pasa wallets_override, convertirlo a total_billeteras_override sumando solo activas no de inversión
    if wallets_override is not None:
        wallets = [
            w for w in wallets_override
            if getattr(w, "estado", EstadoBilletera.ACTIVA) == EstadoBilletera.ACTIVA
            and not getattr(w, "es_inversion", False)
        ]
        if billetera_ids:
            wallets = [w for w in wallets if w.id in billetera_ids]

        override_ars = Decimal("0.00")
        override_usd = Decimal("0.00")
        for w in wallets:
            moneda_val = w.moneda.value if hasattr(w.moneda, "value") else str(w.moneda)
            saldo = Decimal(str(w.saldo_actual or 0))
            if moneda_val == Moneda.ARS.value:
                override_ars += saldo
            elif moneda_val == Moneda.USD.value:
                override_usd += saldo

        total_billeteras_override = {
            "ars": override_ars,
            "usd": override_usd,
        }

    disp_libre = calcular_disponible_libre(
        db=db,
        usuario=usuario,
        fecha_fin_ciclo=fecha_fin_curr,
        hoy=hoy,
        billetera_ids=billetera_ids,
        total_billeteras_override=total_billeteras_override,
    )

    return {
        "ars": {
            "total_billeteras": disp_libre["ars"]["saldo_total"],
            "cuotas_comprometidas": disp_libre["ars"]["cuotas_pendientes"],
            "suscripciones_mensuales": disp_libre["ars"]["suscripciones_pendientes"],
            "facturas_pendientes": disp_libre["ars"]["facturas_pendientes"],
            "saldo_disponible": disp_libre["ars"]["saldo_disponible"],
        },
        "usd": {
            "total_billeteras": disp_libre["usd"]["saldo_total"],
            "cuotas_comprometidas": disp_libre["usd"]["cuotas_pendientes"],
            "suscripciones_mensuales": disp_libre["usd"]["suscripciones_pendientes"],
            "facturas_pendientes": disp_libre["usd"]["facturas_pendientes"],
            "saldo_disponible": disp_libre["usd"]["saldo_disponible"],
        },
        "fecha_fin_ciclo": fecha_fin_curr,
    }


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
    from sqlalchemy.orm import selectinload, joinedload
    from app.models.presupuesto import Presupuesto, EstadoPresupuesto
    from app.services import presupuesto_service
    presupuestos = db.execute(
        select(Presupuesto)
        .options(
            selectinload(Presupuesto.periodos),
            selectinload(Presupuesto.categorias),
            joinedload(Presupuesto.usuario),
        )
        .where(
            Presupuesto.usuario_id == usuario_id,
            Presupuesto.estado == EstadoPresupuesto.ACTIVO,
            Presupuesto.eliminado_en.is_(None),
        )
        .order_by(Presupuesto.nombre)
    ).scalars().all()
    res = []
    for p in presupuestos:
        _periodo, uso = presupuesto_service.uso_actual(db, p)
        usado = float(uso.mostrado) if uso is not None else 0.0
        res.append({"nombre": p.nombre, "limite": float(p.monto), "usado": usado, "moneda": p.moneda.value})
    return res
