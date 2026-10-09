"""
app/services/pagos_proximos_service.py — Servicio de pagos próximos y disponible libre del ciclo actual.

Calcula la lista de pagos próximos hasta el fin del ciclo actual del usuario (suscripciones, cuotas,
resúmenes de tarjetas y facturas) y el disponible libre resultante (saldo total - compromisos impagos).
No importa dashboard_service ni contexto_financiero_service.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, cast, desc, func, literal, or_, select, String
from sqlalchemy.orm import joinedload, Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import EstadoSuscripcion, Suscripcion
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.usuario import Usuario
from app.services import factura_service
from app.services.resumen_tarjeta_service import calcular_resumen_actual
from app.utils.fecha import hoy_argentina


def listar_pagos_proximos(
    db: Session,
    usuario: Usuario,
    fecha_fin_ciclo: date,
    hoy: Optional[date] = None,
    billetera_ids: Optional[List[UUID]] = None,
) -> List[Dict[str, Any]]:
    """
    Lista todos los pagos y cobros comprometidos hasta el fin del ciclo actual (sin recorte).
    Incluye:
    a. Suscripciones activas con próximo cobro <= fecha_fin_ciclo (incluye vencidas impagas).
    b. Cuotas sueltas (sin tarjeta) impagas con vencimiento <= fecha_fin_ciclo.
    c. Por tarjeta activa, el resumen de calcular_resumen_actual si su vencimiento <= fecha_fin_ciclo,
       descontando cuotas cubiertas por pagos de resumen registrados.
    d. Si el resumen de una tarjeta vence después del fin del ciclo pero quedan cuotas de resúmenes anteriores
       vencidas y sin pagar (no cubiertas), incluye un ítem 'Resumen •••• XXXX' vencido.
    e. Facturas de items_proximos_pagos con límite = fecha_fin_ciclo.
    """
    if hoy is None:
        hoy = hoy_argentina()

    moneda_p = usuario.moneda_principal.value if usuario.moneda_principal else "ARS"
    proximos_pagos: List[Dict[str, Any]] = []

    # 1. Subqueries de precio y moneda vigente para suscripciones
    latest_monto_sq = (
        select(HistorialSuscripcion.monto)
        .where(HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .order_by(desc(HistorialSuscripcion.vigente_desde))
        .limit(1)
        .scalar_subquery()
    )
    latest_moneda_sq = (
        select(cast(HistorialSuscripcion.moneda, String))
        .where(HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .order_by(desc(HistorialSuscripcion.vigente_desde))
        .limit(1)
        .scalar_subquery()
    )

    # 2. Suscripciones activas con próximo cobro hasta el fin del ciclo
    s_stmt_where = and_(
        Suscripcion.usuario_id == usuario.id,
        Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        Suscripcion.proximo_cobro <= fecha_fin_ciclo,
    )
    if billetera_ids:
        tarjeta_ids_stmt = select(TarjetaCredito.id).where(TarjetaCredito.billetera_id.in_(billetera_ids))
        s_stmt_where = and_(
            s_stmt_where,
            or_(
                Suscripcion.billetera_id.in_(billetera_ids),
                Suscripcion.tarjeta_id.in_(tarjeta_ids_stmt),
            ),
        )

    s_stmt = select(
        literal("suscripcion").label("item_tipo"),
        cast(Suscripcion.id, String).label("id"),
        Suscripcion.nombre.label("nombre"),
        latest_monto_sq.label("monto"),
        func.coalesce(latest_moneda_sq, cast(literal(moneda_p), String)).label("moneda"),
        Suscripcion.proximo_cobro.label("fecha"),
    ).where(s_stmt_where)

    subs_rows = db.execute(s_stmt).all()
    for r in subs_rows:
        dias_rest = (r.fecha - hoy).days
        proximos_pagos.append({
            "id": r.id,
            "nombre": r.nombre,
            "monto": float(Decimal(str(r.monto or 0))),
            "moneda": r.moneda,
            "fecha_cobro": r.fecha.isoformat(),
            "dias_restantes": dias_rest,
            "tipo": "suscripcion",
            "color": None,
            "red": None,
            "billetera_nombre": None,
            "billetera_id": None,
            "es_vencido": dias_rest < 0,
            "factura_id": None,
            "estado_factura": None,
        })

    # 3. Cuotas sueltas impagas (sin tarjeta de crédito) con vencimiento hasta el fin del ciclo
    c_stmt_where = and_(
        GrupoCuotas.usuario_id == usuario.id,
        GrupoCuotas.tarjeta_id == None,
        Cuota.pagada == False,
        Cuota.fecha_vencimiento <= fecha_fin_ciclo,
    )
    if billetera_ids:
        parent_tx_stmt = select(Transaccion.id).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.billetera_id.in_(billetera_ids),
        )
        c_stmt_where = and_(c_stmt_where, GrupoCuotas.transaccion_padre_id.in_(parent_tx_stmt))

    c_stmt = (
        select(
            cast(Cuota.id, String).label("id"),
            func.coalesce(
                GrupoCuotas.descripcion,
                Subcategoria.nombre,
                Categoria.nombre,
                literal("Cuota"),
            ).label("nombre"),
            Cuota.monto_proyectado.label("monto"),
            cast(GrupoCuotas.moneda, String).label("moneda"),
            Cuota.fecha_vencimiento.label("fecha"),
        )
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .join(Transaccion, GrupoCuotas.transaccion_padre_id == Transaccion.id)
        .join(Categoria, Transaccion.categoria_id == Categoria.id, isouter=True)
        .join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id, isouter=True)
        .where(c_stmt_where)
    )

    cuotas_rows = db.execute(c_stmt).all()
    for r in cuotas_rows:
        dias_rest = (r.fecha - hoy).days
        proximos_pagos.append({
            "id": r.id,
            "nombre": r.nombre,
            "monto": float(Decimal(str(r.monto or 0))),
            "moneda": r.moneda,
            "fecha_cobro": r.fecha.isoformat(),
            "dias_restantes": dias_rest,
            "tipo": "cuota",
            "color": None,
            "red": None,
            "billetera_nombre": None,
            "billetera_id": None,
            "es_vencido": dias_rest < 0,
            "factura_id": None,
            "estado_factura": None,
        })

    # 4. Tarjetas de crédito activas
    tarjetas_query = (
        db.query(TarjetaCredito)
        .options(joinedload(TarjetaCredito.billetera))
        .filter(
            TarjetaCredito.usuario_id == usuario.id,
            TarjetaCredito.estado == EstadoTarjeta.ACTIVA,
        )
    )
    if billetera_ids:
        tarjetas_query = tarjetas_query.filter(TarjetaCredito.billetera_id.in_(billetera_ids))
    tarjetas = tarjetas_query.all()

    # Pre-cargar cuotas para calcular resúmenes
    tarjetas_ids = [t.id for t in tarjetas]
    limite_futuro = fecha_fin_ciclo + timedelta(days=365)
    all_cuotas = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .options(
            joinedload(Cuota.transaccion).joinedload(Transaccion.subcategoria),
            joinedload(Cuota.grupo),
        )
        .filter(
            GrupoCuotas.usuario_id == usuario.id,
            GrupoCuotas.tarjeta_id.in_(tarjetas_ids) if tarjetas_ids else False,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento <= limite_futuro,
        )
        .order_by(Cuota.fecha_vencimiento)
        .all()
    )

    cuotas_por_tarjeta: Dict[UUID, List[Cuota]] = {}
    for c in all_cuotas:
        tid = c.grupo.tarjeta_id if c.grupo else None
        if tid:
            cuotas_por_tarjeta.setdefault(tid, []).append(c)

    # Identificar pagos de resumen registrados para descontar cuotas cubiertas
    pago_resumen_stmt = select(Transaccion.tarjeta_id, Transaccion.pago_resumen_vencimiento).where(
        Transaccion.usuario_id == usuario.id,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.tarjeta_id.isnot(None),
        Transaccion.pago_resumen_vencimiento.isnot(None),
    )
    pagos_resumen = db.execute(pago_resumen_stmt).all()
    max_pago_por_tarjeta: Dict[UUID, date] = {}
    for tid, f_venc in pagos_resumen:
        if f_venc and tid:
            if tid not in max_pago_por_tarjeta or f_venc > max_pago_por_tarjeta[tid]:
                max_pago_por_tarjeta[tid] = f_venc

    for tarjeta in tarjetas:
        resumen_t = calcular_resumen_actual(db, tarjeta, cuotas_preloaded=cuotas_por_tarjeta.get(tarjeta.id, []))
        d_venc = resumen_t.fecha_vencimiento_proximo
        if not d_venc:
            continue

        tarjeta_moneda_val = tarjeta.moneda.value if hasattr(tarjeta.moneda, "value") else str(tarjeta.moneda)
        max_pago = max_pago_por_tarjeta.get(tarjeta.id)

        # Caso 2c: El resumen vence dentro del ciclo actual
        if d_venc <= fecha_fin_ciclo:
            total_t = Decimal(str(getattr(resumen_t, "total_a_pagar_resumen_actual", resumen_t.total_comprometido_resumen_actual)))

            # Descontar cuotas cubiertas por pagos de resumen registrados (misma regla que saldo disponible)
            cuotas_cubiertas_monto = Decimal("0.00")
            if max_pago:
                for ra in resumen_t.resumenes_anteriores:
                    for c_ant in ra.cuotas:
                        if not c_ant.pagada and c_ant.fecha_vencimiento <= max_pago and c_ant.moneda == tarjeta_moneda_val:
                            cuotas_cubiertas_monto += Decimal(str(c_ant.monto))
                for c_act in resumen_t.cuotas_resumen_actual:
                    if not c_act.pagada and c_act.fecha_vencimiento <= max_pago and c_act.moneda == tarjeta_moneda_val:
                        cuotas_cubiertas_monto += Decimal(str(c_act.monto))

            monto_resumen_final = max(Decimal("0.00"), total_t - cuotas_cubiertas_monto)

            if monto_resumen_final > Decimal("0.00"):
                dias_rest = (d_venc - hoy).days
                proximos_pagos.append({
                    "id": str(tarjeta.id),
                    "nombre": f"Resumen {tarjeta.nombre}",
                    "monto": float(monto_resumen_final),
                    "moneda": tarjeta_moneda_val,
                    "fecha_cobro": d_venc.isoformat(),
                    "dias_restantes": dias_rest,
                    "tipo": "resumen_tarjeta",
                    "color": tarjeta.color,
                    "red": tarjeta.red.value if hasattr(tarjeta.red, "value") else str(tarjeta.red),
                    "billetera_nombre": tarjeta.billetera.nombre if tarjeta.billetera else None,
                    "billetera_id": str(tarjeta.billetera_id),
                    "es_vencido": dias_rest < 0,
                    "factura_id": None,
                    "estado_factura": None,
                })

        # Caso 2d: El resumen vence después del fin del ciclo, pero quedan cuotas de resúmenes anteriores vencidas y sin pagar
        else:
            cuotas_vencidas_impagas = []
            for ra in resumen_t.resumenes_anteriores:
                for c_ant in ra.cuotas:
                    if not c_ant.pagada and c_ant.moneda == tarjeta_moneda_val:
                        # Una cuota cubierta por pago de resumen registrado cuenta como pagada
                        if max_pago and c_ant.fecha_vencimiento <= max_pago:
                            continue
                        cuotas_vencidas_impagas.append(c_ant)

            if cuotas_vencidas_impagas:
                monto_total_vencido = sum(Decimal(str(c.monto)) for c in cuotas_vencidas_impagas)
                venc_mas_viejo = min(c.fecha_vencimiento for c in cuotas_vencidas_impagas)
                dias_rest = (venc_mas_viejo - hoy).days

                proximos_pagos.append({
                    "id": str(tarjeta.id),
                    "nombre": f"Resumen {tarjeta.nombre}",
                    "monto": float(monto_total_vencido),
                    "moneda": tarjeta_moneda_val,
                    "fecha_cobro": venc_mas_viejo.isoformat(),
                    "dias_restantes": dias_rest,
                    "tipo": "resumen_tarjeta",
                    "color": tarjeta.color,
                    "red": tarjeta.red.value if hasattr(tarjeta.red, "value") else str(tarjeta.red),
                    "billetera_nombre": tarjeta.billetera.nombre if tarjeta.billetera else None,
                    "billetera_id": str(tarjeta.billetera_id),
                    "es_vencido": True,
                    "factura_id": None,
                    "estado_factura": None,
                })

    # 5. Facturas de items_proximos_pagos con límite = fecha_fin_ciclo
    facturas_items = factura_service.items_proximos_pagos(db, usuario.id, hoy, fecha_fin_ciclo)
    proximos_pagos.extend(facturas_items)

    # 6. Ordenar lista completa: primero vencidos (por fecha ascendente), luego próximos (por fecha ascendente)
    proximos_pagos.sort(
        key=lambda x: (0 if x["dias_restantes"] < 0 else 1, x["fecha_cobro"])
    )

    return proximos_pagos


def calcular_disponible_libre(
    db: Session,
    usuario: Usuario,
    fecha_fin_ciclo: date,
    hoy: Optional[date] = None,
    billetera_ids: Optional[List[UUID]] = None,
    total_billeteras_override: Optional[Dict[str, Decimal]] = None,
    pagos: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Calcula el disponible libre por moneda a partir del saldo total y todos los pagos próximos sin pagar
    (usando la lista completa, sin recorte a 5).
    """
    if hoy is None:
        hoy = hoy_argentina()

    if pagos is None:
        pagos = listar_pagos_proximos(
            db=db,
            usuario=usuario,
            fecha_fin_ciclo=fecha_fin_ciclo,
            hoy=hoy,
            billetera_ids=billetera_ids,
        )

    # 1. Saldo total en billeteras activas no de inversión
    saldo_total = {"ars": Decimal("0.00"), "usd": Decimal("0.00")}
    if total_billeteras_override:
        saldo_total["ars"] = Decimal(str(total_billeteras_override.get("ars", Decimal("0.00"))))
        saldo_total["usd"] = Decimal(str(total_billeteras_override.get("usd", Decimal("0.00"))))
    else:
        b_stmt = select(Billetera.moneda, func.sum(Billetera.saldo_actual)).where(
            Billetera.usuario_id == usuario.id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.es_inversion == False,
        )
        if billetera_ids:
            b_stmt = b_stmt.where(Billetera.id.in_(billetera_ids))
        b_stmt = b_stmt.group_by(Billetera.moneda)
        for m, s in db.execute(b_stmt).all():
            m_key = m.value.lower() if hasattr(m, "value") else str(m).lower()
            if m_key in saldo_total:
                saldo_total[m_key] = Decimal(str(s or Decimal("0.00")))

    # 2. Desglose de compromisos impagos por moneda
    resultado: Dict[str, Any] = {}

    for m_key in ("ars", "usd"):
        cuotas_pendientes = Decimal("0.00")
        suscripciones_pendientes = Decimal("0.00")
        facturas_pendientes = Decimal("0.00")
        compromisos: List[Dict[str, Any]] = []

        for p in pagos:
            moneda_pago = str(p.get("moneda", "")).lower()
            if moneda_pago != m_key:
                continue

            # Las facturas pagadas no se restan del disponible libre
            if p.get("tipo") == "factura" and p.get("estado_factura") == "pagada":
                continue

            monto_dec = Decimal(str(p.get("monto") or 0))

            if p.get("tipo") in ("cuota", "resumen_tarjeta"):
                cuotas_pendientes += monto_dec
            elif p.get("tipo") == "suscripcion":
                suscripciones_pendientes += monto_dec
            elif p.get("tipo") == "factura":
                facturas_pendientes += monto_dec

            compromisos.append({
                "id": str(p.get("id")),
                "tipo": str(p.get("tipo")),
                "nombre": str(p.get("nombre")),
                "monto": monto_dec,
                "fecha_cobro": str(p.get("fecha_cobro")),
            })

        saldo_disp = saldo_total[m_key] - cuotas_pendientes - suscripciones_pendientes - facturas_pendientes

        resultado[m_key] = {
            "saldo_total": saldo_total[m_key],
            "cuotas_pendientes": cuotas_pendientes,
            "suscripciones_pendientes": suscripciones_pendientes,
            "facturas_pendientes": facturas_pendientes,
            "saldo_disponible": saldo_disp,
            "compromisos": compromisos,
        }

    return resultado
