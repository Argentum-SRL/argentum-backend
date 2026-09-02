from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import HTTPException
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, select, desc, or_, case, literal, null, String, cast, union_all
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.utils.fecha import hoy_argentina
from app.models.usuario import Usuario, CicloTipo, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, MetodoPago
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.services.tarjeta_service import calcular_resumen_actual

def get_date_by_rule(rule: str, month: int, year: int) -> date:
    """Calcula la fecha exacta segun una regla (ej: ultimo_viernes)."""
    parts = rule.lower().split("_")
    if len(parts) != 2:
        return date(year, month, 1)
    
    when, weekday_str = parts[0], parts[1]
    weekdays = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}
    target_weekday = weekdays.get(weekday_str)
    if target_weekday is None:
        return date(year, month, 1)
        
    first_day = date(year, month, 1)
    last_day = (first_day + relativedelta(months=1)) - timedelta(days=1)
    
    if when == "primer":
        d = first_day
        while d.weekday() != target_weekday:
            d += timedelta(days=1)
        return d
    elif when == "ultimo":
        d = last_day
        while d.weekday() != target_weekday:
            d -= timedelta(days=1)
        return d
    return first_day

def get_ciclo_fechas(usuario: Usuario, hoy: date) -> tuple[date, date]:
    """Calcula fecha_inicio y fecha_fin del ciclo actual del usuario."""
    if not usuario.ciclo_tipo or not usuario.ciclo_valor:
        inicio = hoy.replace(day=1)
        fin = (inicio + relativedelta(months=1)) - timedelta(days=1)
        return inicio, fin

    ciclo_dir = getattr(usuario, "ciclo_ajuste_direccion", None)
    direccion = (
        ciclo_dir.value
        if hasattr(ciclo_dir, "value")
        else (str(ciclo_dir) if ciclo_dir else "anterior")
    )

    if usuario.ciclo_tipo == CicloTipo.DIA_FIJO:
        from app.services.dias_habiles_service import calcular_fecha_cobro_sync
        try:
            dia = int(usuario.ciclo_valor)
        except ValueError:
            dia = 1
        
        # Calcular inicio del ciclo actual con ajuste de día hábil
        inicio_candidato_este_mes = calcular_fecha_cobro_sync(dia, hoy.month, hoy.year, direccion=direccion)
        if hoy >= inicio_candidato_este_mes:
            # El ciclo comenzó este mes
            inicio = inicio_candidato_este_mes
        else:
            # El ciclo comenzó el mes anterior
            prev_month = hoy - relativedelta(months=1)
            inicio = calcular_fecha_cobro_sync(dia, prev_month.month, prev_month.year, direccion=direccion)
        
        # El ciclo termina el día antes del próximo inicio
        proximo_mes = inicio + relativedelta(months=1)
        proximo_inicio = calcular_fecha_cobro_sync(dia, proximo_mes.month, proximo_mes.year, direccion=direccion)
        fin = proximo_inicio - timedelta(days=1)
        
        return inicio, fin

    if usuario.ciclo_tipo == CicloTipo.REGLA:
        from app.services.dias_habiles_service import ajustar_fecha_habil_sync
        d_nominal_este_mes = get_date_by_rule(usuario.ciclo_valor, hoy.month, hoy.year)
        d_regla = ajustar_fecha_habil_sync(d_nominal_este_mes, direccion=direccion)
        if hoy >= d_regla:
            inicio = d_regla
        else:
            prev = hoy - relativedelta(months=1)
            d_nominal_prev = get_date_by_rule(usuario.ciclo_valor, prev.month, prev.year)
            inicio = ajustar_fecha_habil_sync(d_nominal_prev, direccion=direccion)
        prox = inicio + relativedelta(months=1)
        d_nominal_prox = get_date_by_rule(usuario.ciclo_valor, prox.month, prox.year)
        proximo_inicio = ajustar_fecha_habil_sync(d_nominal_prox, direccion=direccion)
        fin = proximo_inicio - timedelta(days=1)
        return inicio, fin

    return hoy.replace(day=1), (hoy.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)

def get_dashboard_resumen(
    db: Session, 
    usuario: Usuario, 
    fecha_desde_override: Optional[date] = None, 
    fecha_hasta_override: Optional[date] = None,
    total_billeteras_override: Optional[Dict[str, Decimal]] = None,
    billetera_ids: Optional[List[UUID]] = None
) -> Dict[str, Any]:
    """
    Retorna el resumen optimizado del dashboard en máximo 2 queries DB.
    """
    hoy = hoy_argentina()
    fecha_inicio, fecha_fin = (fecha_desde_override, fecha_hasta_override) if (fecha_desde_override and fecha_hasta_override) else get_ciclo_fechas(usuario, hoy)
    fecha_inicio_ant, fecha_fin_ant = get_ciclo_fechas(usuario, fecha_inicio - timedelta(days=1))
    fecha_inicio_prox, fecha_fin_prox = get_ciclo_fechas(usuario, fecha_fin + timedelta(days=1))
    limite_pagos = hoy + timedelta(days=30)
    moneda_p = usuario.moneda_principal.value if usuario.moneda_principal else "ARS"

    # --- QUERY 1: Balances, Totales y Estadísticas Globales ---
    cycle_actual_cond = and_(Transaccion.fecha >= fecha_inicio, Transaccion.fecha <= fecha_fin)
    cycle_ant_cond = and_(Transaccion.fecha >= fecha_inicio_ant, Transaccion.fecha <= fecha_fin_ant)

    res_stmt_where = and_(
        Transaccion.usuario_id == usuario.id,
        Transaccion.es_padre_cuotas == False,
        Transaccion.metodo_pago.is_distinct_from(MetodoPago.CREDITO),
        or_(Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA, Transaccion.estado_verificacion == None)
    )
    if billetera_ids:
        res_stmt_where = and_(res_stmt_where, Transaccion.billetera_id.in_(billetera_ids))

    res_stmt = select(
        func.min(Transaccion.fecha).label("primera_tx"),
        # ARS actual
        func.sum(case((and_(cycle_actual_cond, Transaccion.moneda == Moneda.ARS, Transaccion.tipo == TipoTransaccion.INGRESO), Transaccion.monto), else_=0)).label("ing_actual_ars"),
        func.sum(case((and_(cycle_actual_cond, Transaccion.moneda == Moneda.ARS, Transaccion.tipo == TipoTransaccion.EGRESO), Transaccion.monto), else_=0)).label("egr_actual_ars"),
        # ARS anterior
        func.sum(case((and_(cycle_ant_cond, Transaccion.moneda == Moneda.ARS, Transaccion.tipo == TipoTransaccion.INGRESO), Transaccion.monto), else_=0)).label("ing_ant_ars"),
        func.sum(case((and_(cycle_ant_cond, Transaccion.moneda == Moneda.ARS, Transaccion.tipo == TipoTransaccion.EGRESO), Transaccion.monto), else_=0)).label("egr_ant_ars"),
        # USD actual
        func.sum(case((and_(cycle_actual_cond, Transaccion.moneda == Moneda.USD, Transaccion.tipo == TipoTransaccion.INGRESO), Transaccion.monto), else_=0)).label("ing_actual_usd"),
        func.sum(case((and_(cycle_actual_cond, Transaccion.moneda == Moneda.USD, Transaccion.tipo == TipoTransaccion.EGRESO), Transaccion.monto), else_=0)).label("egr_actual_usd"),
        # USD anterior
        func.sum(case((and_(cycle_ant_cond, Transaccion.moneda == Moneda.USD, Transaccion.tipo == TipoTransaccion.INGRESO), Transaccion.monto), else_=0)).label("ing_ant_usd"),
        func.sum(case((and_(cycle_ant_cond, Transaccion.moneda == Moneda.USD, Transaccion.tipo == TipoTransaccion.EGRESO), Transaccion.monto), else_=0)).label("egr_ant_usd")
    ).where(res_stmt_where)
    res = db.execute(res_stmt).one()

    # --- QUERY 2: Actividad Unificada (Movimientos + Pagos) ---
    latest_monto_sq = (
        select(HistorialSuscripcion.monto).where(HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .order_by(desc(HistorialSuscripcion.vigente_desde)).limit(1).scalar_subquery()
    )

    m_stmt_where = and_(
        Transaccion.usuario_id == usuario.id,
        Transaccion.fecha >= fecha_inicio,
        Transaccion.fecha <= fecha_fin,
        Transaccion.es_padre_cuotas == False,
        Transaccion.metodo_pago.is_distinct_from(MetodoPago.CREDITO)
    )
    if billetera_ids:
        m_stmt_where = and_(m_stmt_where, Transaccion.billetera_id.in_(billetera_ids))

    m_stmt = select(
        literal("movimiento").label("item_tipo"),
        cast(Transaccion.id, String).label("id"),
        Transaccion.descripcion.label("nombre"),
        Transaccion.monto.label("monto"),
        cast(Transaccion.moneda, String).label("moneda"),
        Transaccion.fecha.label("fecha"),
        Categoria.nombre.label("extra_1"), # categoria_nombre
        Billetera.nombre.label("extra_2"), # billetera_nombre
        cast(Transaccion.estado_verificacion, String).label("extra_3"), # estado_verificacion
        cast(Transaccion.tipo, String).label("extra_4"), # tipo_transaccion
        Subcategoria.nombre.label("extra_5") # subcategoria_nombre
    ).join(Categoria, Transaccion.categoria_id == Categoria.id, isouter=True)\
     .join(Billetera, Transaccion.billetera_id == Billetera.id, isouter=True)\
     .join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id, isouter=True).where(m_stmt_where)\
     .order_by(desc(Transaccion.fecha), desc(Transaccion.fecha_creacion)).limit(6)

    s_stmt_where = and_(
        Suscripcion.usuario_id == usuario.id,
        Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        Suscripcion.proximo_cobro >= hoy,
        Suscripcion.proximo_cobro <= limite_pagos
    )
    if billetera_ids:
        tarjeta_ids_stmt = select(TarjetaCredito.id).where(TarjetaCredito.billetera_id.in_(billetera_ids))
        s_stmt_where = and_(
            s_stmt_where,
            or_(
                Suscripcion.billetera_id.in_(billetera_ids),
                Suscripcion.tarjeta_id.in_(tarjeta_ids_stmt)
            )
        )

    s_stmt = select(
        literal("suscripcion").label("item_tipo"),
        cast(Suscripcion.id, String).label("id"),
        Suscripcion.nombre.label("nombre"),
        latest_monto_sq.label("monto"),
        cast(literal(moneda_p), String).label("moneda"),
        Suscripcion.proximo_cobro.label("fecha"),
        cast(null(), String).label("extra_1"),
        cast(null(), String).label("extra_2"),
        cast(null(), String).label("extra_3"),
        cast(null(), String).label("extra_4"),
        cast(null(), String).label("extra_5")
    ).where(s_stmt_where)

    c_stmt_where = and_(
        GrupoCuotas.usuario_id == usuario.id,
        Cuota.pagada == False,
        Cuota.fecha_vencimiento >= hoy,
        Cuota.fecha_vencimiento <= limite_pagos
    )
    if billetera_ids:
        tarjeta_ids_stmt = select(TarjetaCredito.id).where(TarjetaCredito.billetera_id.in_(billetera_ids))
        parent_tx_stmt = select(Transaccion.id).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.billetera_id.in_(billetera_ids)
        )
        c_stmt_where = and_(
            c_stmt_where,
            or_(
                GrupoCuotas.tarjeta_id.in_(tarjeta_ids_stmt),
                and_(
                    GrupoCuotas.tarjeta_id == None,
                    GrupoCuotas.transaccion_padre_id.in_(parent_tx_stmt)
                )
            )
        )

    c_stmt = select(
        literal("cuota").label("item_tipo"),
        cast(Cuota.id, String).label("id"),
        func.coalesce(
            GrupoCuotas.descripcion, 
            Subcategoria.nombre,
            Categoria.nombre,
            literal("Cuota")
        ).label("nombre"),
        Cuota.monto_proyectado.label("monto"),
        cast(GrupoCuotas.moneda, String).label("moneda"),
        Cuota.fecha_vencimiento.label("fecha"),
        cast(null(), String).label("extra_1"),
        cast(GrupoCuotas.tarjeta_id, String).label("extra_2"),
        cast(null(), String).label("extra_3"),
        cast(null(), String).label("extra_4"),
        cast(null(), String).label("extra_5")
    ).join(GrupoCuotas)\
     .join(Transaccion, GrupoCuotas.transaccion_padre_id == Transaccion.id)\
     .join(Categoria, Transaccion.categoria_id == Categoria.id, isouter=True)\
     .join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id, isouter=True)\
     .where(c_stmt_where)

    actividad = db.execute(m_stmt.union_all(s_stmt, c_stmt)).all()

    # --- Procesamiento de Resultados ---
    # ARS
    ing_actual_ars = res.ing_actual_ars or Decimal("0")
    egr_actual_ars = res.egr_actual_ars or Decimal("0")
    ing_ant_ars = res.ing_ant_ars or Decimal("0")
    egr_ant_ars = res.egr_ant_ars or Decimal("0")
    balance_ars = ing_actual_ars - egr_actual_ars
    balance_ant_ars = ing_ant_ars - egr_ant_ars
    variacion_ars = round(float(((balance_ars - balance_ant_ars) / abs(balance_ant_ars)) * 100), 1) if balance_ant_ars != 0 else None

    # USD
    ing_actual_usd = res.ing_actual_usd or Decimal("0")
    egr_actual_usd = res.egr_actual_usd or Decimal("0")
    ing_ant_usd = res.ing_ant_usd or Decimal("0")
    egr_ant_usd = res.egr_ant_usd or Decimal("0")
    balance_usd = ing_actual_usd - egr_actual_usd
    balance_ant_usd = ing_ant_usd - egr_ant_usd
    variacion_usd = round(float(((balance_usd - balance_ant_usd) / abs(balance_ant_usd)) * 100), 1) if balance_ant_usd != 0 else None

    movimientos_data = [{
        "id": r.id, "descripcion": r.nombre, "fecha": r.fecha.isoformat(), "monto": float(r.monto),
        "tipo": r.extra_4, "moneda": r.moneda, "billetera_nombre": r.extra_2 or "Billetera",
        "categoria_nombre": r.extra_1, "estado_verificacion": r.extra_3,
        "subcategoria_nombre": r.extra_5
    } for r in actividad if r.item_tipo == "movimiento"]

    proximos_pagos = [{
        "id": r.id, "nombre": r.nombre, "monto": float(r.monto or 0), "moneda": r.moneda,
        "fecha_cobro": r.fecha.isoformat(), "dias_restantes": (r.fecha - hoy).days, "tipo": r.item_tipo
    } for r in actividad if r.item_tipo in ("suscripcion", "cuota") and not (r.item_tipo == "cuota" and r.extra_2)]

    # --- AGREGAR VENCIMIENTOS DE TARJETAS ---
    tarjetas_query = db.query(TarjetaCredito).options(
        joinedload(TarjetaCredito.billetera)
    ).filter(
        TarjetaCredito.usuario_id == usuario.id,
        TarjetaCredito.estado == EstadoTarjeta.ACTIVA
    )
    if billetera_ids:
        tarjetas_query = tarjetas_query.filter(TarjetaCredito.billetera_id.in_(billetera_ids))
    tarjetas = tarjetas_query.all()

    limite_futuro = hoy + timedelta(days=365)

    # Optimizacion N+1: Pre-cargar todas las cuotas futuras de todas las tarjetas activas
    tarjetas_ids = [t.id for t in tarjetas]
    all_cuotas = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .options(
            joinedload(Cuota.transaccion).joinedload(Transaccion.subcategoria),
            joinedload(Cuota.grupo)
        )
        .filter(
            GrupoCuotas.usuario_id == usuario.id,
            GrupoCuotas.tarjeta_id.in_(tarjetas_ids) if tarjetas_ids else False,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento >= hoy,
            Cuota.fecha_vencimiento <= limite_futuro
        )
        .order_by(Cuota.fecha_vencimiento)
        .all()
    )

    cuotas_por_tarjeta = {}
    for c in all_cuotas:
        tid = c.grupo.tarjeta_id
        if tid not in cuotas_por_tarjeta:
            cuotas_por_tarjeta[tid] = []
        cuotas_por_tarjeta[tid].append(c)

    for tarjeta in tarjetas:
        resumen_t = calcular_resumen_actual(db, tarjeta, cuotas_preloaded=cuotas_por_tarjeta.get(tarjeta.id, []))
        total_t = resumen_t.total_comprometido_resumen_actual
        total_siguiente = resumen_t.total_comprometido_resumen_siguiente if hasattr(resumen_t, 'total_comprometido_resumen_siguiente') else 0

        d_venc = resumen_t.fecha_vencimiento_proximo
        if not d_venc:
            continue

        # Si el resumen actual es 0 pero hay deuda en el siguiente período,
        # mostrar el próximo resumen con deuda real
        if total_t <= 0:
            if total_siguiente and total_siguiente > 0:
                # Calcular fecha del siguiente vencimiento
                proximo_mes = d_venc + relativedelta(months=1)
                from calendar import monthrange
                ultimo_dia = monthrange(proximo_mes.year, proximo_mes.month)[1]
                dia_venc_sig = min(tarjeta.dia_vencimiento, ultimo_dia)
                d_venc_sig = date(proximo_mes.year, proximo_mes.month, dia_venc_sig)
                dias_restantes_sig = (d_venc_sig - hoy).days
                if 0 <= dias_restantes_sig <= 60:
                    proximos_pagos.append({
                        "id": str(tarjeta.id),
                        "nombre": f"Resumen {tarjeta.nombre}",
                        "monto": float(total_siguiente),
                        "moneda": tarjeta.moneda.value,
                        "fecha_cobro": d_venc_sig.isoformat(),
                        "dias_restantes": dias_restantes_sig,
                        "tipo": "resumen_tarjeta",
                        "color": tarjeta.color,
                        "red": tarjeta.red.value,
                        "billetera_nombre": tarjeta.billetera.nombre,
                        "billetera_id": str(tarjeta.billetera_id)
                    })
            continue

        dias_restantes = (d_venc - hoy).days

        # Solo incluir si vence dentro de los próximos 45 días
        if 0 <= dias_restantes <= 45:
            proximos_pagos.append({
                "id": str(tarjeta.id),
                "nombre": f"Resumen {tarjeta.nombre}",
                "monto": float(total_t),
                "moneda": tarjeta.moneda.value,
                "fecha_cobro": d_venc.isoformat(),
                "dias_restantes": dias_restantes,
                "tipo": "resumen_tarjeta",
                "color": tarjeta.color,
                "red": tarjeta.red.value,
                "billetera_nombre": tarjeta.billetera.nombre,
                "billetera_id": str(tarjeta.billetera_id)
            })

    from app.services.contexto_financiero_service import _calcular_saldo_disponible_sync
    disp_ctx = _calcular_saldo_disponible_sync(db, usuario.id, billetera_ids)
    if total_billeteras_override:
        disp_ctx["ars"]["total_billeteras"] = total_billeteras_override.get("ars", Decimal("0"))
        disp_ctx["usd"]["total_billeteras"] = total_billeteras_override.get("usd", Decimal("0"))
        disp_ctx["ars"]["saldo_disponible"] = disp_ctx["ars"]["total_billeteras"] - disp_ctx["ars"]["cuotas_comprometidas"] - disp_ctx["ars"]["suscripciones_mensuales"]
        disp_ctx["usd"]["saldo_disponible"] = disp_ctx["usd"]["total_billeteras"] - disp_ctx["usd"]["cuotas_comprometidas"] - disp_ctx["usd"]["suscripciones_mensuales"]
    proximos_pagos = sorted(proximos_pagos, key=lambda x: x["fecha_cobro"])[:5]

    return {
        "periodo": {
            "fecha_inicio": fecha_inicio.isoformat(), "fecha_fin": fecha_fin.isoformat(),
            "primera_transaccion": res.primera_tx.isoformat() if res.primera_tx else None
        },
        "balance": {
            "ars": {
                "ingresos": float(ing_actual_ars),
                "egresos": float(egr_actual_ars),
                "balance": float(balance_ars),
                "variacion_vs_ciclo_anterior": variacion_ars
            },
            "usd": {
                "ingresos": float(ing_actual_usd),
                "egresos": float(egr_actual_usd),
                "balance": float(balance_usd),
                "variacion_vs_ciclo_anterior": variacion_usd
            }
        },
        "disponible_real": {
            "ars": {
                "saldo_billeteras": float(disp_ctx["ars"]["total_billeteras"]),
                "cuotas_proximo_ciclo": float(disp_ctx["ars"]["cuotas_comprometidas"]),
                "suscripciones_mensuales": float(disp_ctx["ars"]["suscripciones_mensuales"]),
                "disponible": float(disp_ctx["ars"]["saldo_disponible"])
            },
            "usd": {
                "saldo_billeteras": float(disp_ctx["usd"]["total_billeteras"]),
                "cuotas_proximo_ciclo": float(disp_ctx["usd"]["cuotas_comprometidas"]),
                "suscripciones_mensuales": float(disp_ctx["usd"]["suscripciones_mensuales"]),
                "disponible": float(disp_ctx["usd"]["saldo_disponible"])
            }
        },
        "ultimos_movimientos": movimientos_data,
        "proximos_pagos": proximos_pagos
    }

def get_cotizacion_usuario(usuario: Usuario) -> Dict[str, Any]:
    from app.services.dolar_service import get_cotizaciones_dolar
    tipo = (usuario.tipo_dolar or "blue").lower()
    try:
        data = get_cotizaciones_dolar()
        cots = data.get("cotizaciones", {})
        if tipo in cots:
            return cots[tipo]
        if "blue" in cots:
            return cots["blue"]
        if "oficial" in cots:
            return cots["oficial"]
    except Exception:
        pass

    return {
        "tipo": tipo,
        "nombre": f"Dólar {tipo.capitalize()}",
        "compra": None,
        "venta": None,
        "promedio": None,
        "moneda": "ARS",
        "fecha_actualizacion": None,
        "error": "Servicio de cotizaciones no disponible"
    }


def get_resumen_completo(
    db: Session, 
    usuario: Usuario, 
    desde: Optional[date] = None, 
    hasta: Optional[date] = None,
    billetera_ids: Optional[List[UUID]] = None
) -> Dict[str, Any]:
    """
    Consolida todo el dashboard en exactamente 3 queries DB.
    """
    # QUERY 1: Billeteras y su estado de actividad
    from sqlalchemy import exists
    from app.models.transferencia_interna import TransferenciaInterna
    
    exists_tx = exists().where(Transaccion.billetera_id == Billetera.id)
    exists_tr = exists().where((TransferenciaInterna.billetera_origen_id == Billetera.id) | (TransferenciaInterna.billetera_destino_id == Billetera.id))
    
    stmt_billeteras = select(Billetera, (exists_tx | exists_tr).label("has_tx")).where(Billetera.usuario_id == usuario.id)
    rows_billeteras = db.execute(stmt_billeteras).all()
    
    billeteras_data = []
    total_saldo_activa = {"ars": Decimal("0"), "usd": Decimal("0")}
    for b, has_tx in rows_billeteras:
        if b.estado == EstadoBilletera.ACTIVA:
            if not billetera_ids or b.id in billetera_ids:
                moneda_key = b.moneda.value.lower()
                if moneda_key in total_saldo_activa:
                    total_saldo_activa[moneda_key] += Decimal(str(b.saldo_actual))
        billeteras_data.append({
            "id": str(b.id),
            "nombre": b.nombre,
            "moneda": b.moneda.value,
            "saldo_actual": float(b.saldo_actual),
            "saldo_inicial": float(getattr(b, "saldo_inicial", Decimal("0")) or Decimal("0")),
            "es_principal": bool(b.es_principal),
            "es_efectivo": bool(b.es_efectivo),
            "estado": b.estado.value,
            "fecha_creacion": b.fecha_creacion.isoformat() if getattr(b, "fecha_creacion", None) else None,
            "bank_id": getattr(b, "bank_id", None),
            "tiene_transacciones": bool(has_tx)
        })

    # QUERY 2 y 3: Se ejecutan dentro de get_dashboard_resumen
    resumen = get_dashboard_resumen(db, usuario, desde, hasta, total_billeteras_override=total_saldo_activa, billetera_ids=billetera_ids)
    cotizacion = get_cotizacion_usuario(usuario)

    return {"billeteras": billeteras_data, "resumen": resumen, "cotizacion": cotizacion}

def get_subcategorias_gasto(
    db: Session,
    usuario: Usuario,
    categoria_id: str,
    billetera_ids: Optional[List[UUID]] = None
) -> List[Dict[str, Any]]:
    """
    Retorna los gastos por subcategoría de una categoría específica en el ciclo actual.
    """
    import uuid
    try:
        cat_uuid = uuid.UUID(categoria_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Formato de ID de categoría inválido")

    hoy = hoy_argentina()
    fecha_inicio, fecha_fin = get_ciclo_fechas(usuario, hoy)

    # 1. Obtener todas las subcategorías activas de la categoría
    subcategorias_stmt = select(Subcategoria).where(
        and_(
            Subcategoria.categoria_id == cat_uuid,
            Subcategoria.estado == EstadoSubcategoria.ACTIVA
        )
    )
    subcategorias = db.execute(subcategorias_stmt).scalars().all()
    sub_map = {sub.id: sub.nombre for sub in subcategorias}

    # 2. Agrupar gastos de transacciones por subcategoria_id en el ciclo actual
    tx_where = and_(
        Transaccion.usuario_id == usuario.id,
        Transaccion.categoria_id == cat_uuid,
        Transaccion.fecha >= fecha_inicio,
        Transaccion.fecha <= fecha_fin,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.es_padre_cuotas == False,
        Transaccion.metodo_pago.is_distinct_from(MetodoPago.CREDITO),
        or_(
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.estado_verificacion == None
        )
    )
    if billetera_ids:
        tx_where = and_(tx_where, Transaccion.billetera_id.in_(billetera_ids))

    stmt = (
        select(
            Transaccion.subcategoria_id,
            Transaccion.moneda,
            func.sum(Transaccion.monto).label("total")
        )
        .where(tx_where)
        .group_by(Transaccion.subcategoria_id, Transaccion.moneda)
    )
    res = db.execute(stmt).all()

    # 3. Consolidar resultados
    sub_gastos = {}
    general_total = {"ars": Decimal("0"), "usd": Decimal("0")}
    for row in res:
        sub_id = row.subcategoria_id
        moneda_val = row.moneda.value.lower() if row.moneda else "ars"
        total = row.total or Decimal("0")
        if sub_id in sub_map:
            if sub_id not in sub_gastos:
                sub_gastos[sub_id] = {"ars": Decimal("0"), "usd": Decimal("0")}
            if moneda_val in sub_gastos[sub_id]:
                sub_gastos[sub_id][moneda_val] += total
        else:
            if moneda_val in general_total:
                general_total[moneda_val] += total

    desglose = []
    for sub in subcategorias:
        gasto_dict = sub_gastos.get(sub.id, {"ars": Decimal("0"), "usd": Decimal("0")})
        desglose.append({
            "subcategoria_id": str(sub.id),
            "subcategoria_nombre": sub.nombre,
            "gasto_actual_ciclo": {
                "ars": float(gasto_dict["ars"]),
                "usd": float(gasto_dict["usd"])
            }
        })

    if general_total["ars"] > 0 or general_total["usd"] > 0:
        desglose.append({
            "subcategoria_id": "general",
            "subcategoria_nombre": "General",
            "gasto_actual_ciclo": {
                "ars": float(general_total["ars"]),
                "usd": float(general_total["usd"])
            }
        })

    desglose.sort(key=lambda x: (
        -(x["gasto_actual_ciclo"]["ars"] + x["gasto_actual_ciclo"]["usd"] * 1000),
        x["subcategoria_nombre"]
    ))
    return desglose
