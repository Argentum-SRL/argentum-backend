from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import HTTPException
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, select, desc, or_, case, literal, null, String, cast, union_all
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.usuario import Usuario, CicloTipo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
from app.models.categoria import Categoria
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion

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

    if usuario.ciclo_tipo == CicloTipo.DIA_FIJO:
        try:
            dia = int(usuario.ciclo_valor)
        except ValueError:
            dia = 1
        
        last_of_month = (hoy.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
        dia_ajustado = min(dia, last_of_month.day)
        
        if hoy.day >= dia_ajustado:
            inicio = hoy.replace(day=dia_ajustado)
        else:
            prev_month = hoy - relativedelta(months=1)
            last_of_prev = (prev_month.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
            inicio = prev_month.replace(day=min(dia, last_of_prev.day))
        
        proximo_inicio = inicio + relativedelta(months=1)
        last_of_next = (proximo_inicio.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
        fin = proximo_inicio.replace(day=min(dia, last_of_next.day)) - timedelta(days=1)
        return inicio, fin

    if usuario.ciclo_tipo == CicloTipo.REGLA:
        d_regla = get_date_by_rule(usuario.ciclo_valor, hoy.month, hoy.year)
        if hoy >= d_regla:
            inicio = d_regla
        else:
            prev = hoy - relativedelta(months=1)
            inicio = get_date_by_rule(usuario.ciclo_valor, prev.month, prev.year)
        prox = inicio + relativedelta(months=1)
        fin = get_date_by_rule(usuario.ciclo_valor, prox.month, prox.year) - timedelta(days=1)
        return inicio, fin

    return hoy.replace(day=1), (hoy.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)

def get_dashboard_resumen(
    db: Session, 
    usuario: Usuario, 
    fecha_desde_override: Optional[date] = None, 
    fecha_hasta_override: Optional[date] = None,
    total_billeteras_override: Optional[Decimal] = None
) -> Dict[str, Any]:
    """
    Retorna el resumen optimizado del dashboard en máximo 2 queries DB.
    """
    hoy = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    fecha_inicio, fecha_fin = (fecha_desde_override, fecha_hasta_override) if (fecha_desde_override and fecha_hasta_override) else get_ciclo_fechas(usuario, hoy)
    fecha_inicio_ant, fecha_fin_ant = get_ciclo_fechas(usuario, fecha_inicio - timedelta(days=1))
    fecha_inicio_prox, fecha_fin_prox = get_ciclo_fechas(usuario, fecha_fin + timedelta(days=1))
    limite_pagos = hoy + timedelta(days=30)
    moneda_p = usuario.moneda_principal.value if usuario.moneda_principal else "ARS"

    # --- QUERY 1: Balances, Totales y Estadísticas Globales ---
    cycle_actual_cond = and_(Transaccion.fecha >= fecha_inicio, Transaccion.fecha <= fecha_fin)
    cycle_ant_cond = and_(Transaccion.fecha >= fecha_inicio_ant, Transaccion.fecha <= fecha_fin_ant)
    
    sub_total_billeteras = literal(total_billeteras_override) if total_billeteras_override is not None else (
        select(func.sum(Billetera.saldo_actual))
        .where(and_(Billetera.usuario_id == usuario.id, Billetera.estado == EstadoBilletera.ACTIVA))
        .scalar_subquery()
    )

    res_stmt = select(
        func.min(Transaccion.fecha).label("primera_tx"),
        func.sum(case((cycle_actual_cond, case((Transaccion.tipo == TipoTransaccion.INGRESO, Transaccion.monto), else_=0)), else_=0)).label("ing_actual"),
        func.sum(case((cycle_actual_cond, case((Transaccion.tipo == TipoTransaccion.EGRESO, Transaccion.monto), else_=0)), else_=0)).label("egr_actual"),
        func.sum(case((cycle_ant_cond, case((Transaccion.tipo == TipoTransaccion.INGRESO, Transaccion.monto), else_=0)), else_=0)).label("ing_ant"),
        func.sum(case((cycle_ant_cond, case((Transaccion.tipo == TipoTransaccion.EGRESO, Transaccion.monto), else_=0)), else_=0)).label("egr_ant"),
        sub_total_billeteras.label("total_billeteras"),
        (
            select(func.sum(Cuota.monto_proyectado))
            .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
            .where(and_(
                GrupoCuotas.usuario_id == usuario.id,
                Cuota.pagada == False,
                Cuota.fecha_vencimiento >= fecha_inicio_prox,
                Cuota.fecha_vencimiento <= fecha_fin_prox
            )).scalar_subquery()
        ).label("cuotas_comprometidas")
    ).where(
        and_(
            Transaccion.usuario_id == usuario.id,
            Transaccion.es_padre_cuotas == False,
            or_(Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA, Transaccion.estado_verificacion == None)
        )
    )
    res = db.execute(res_stmt).one()

    # --- QUERY 2: Actividad Unificada (Movimientos + Pagos) ---
    latest_monto_sq = (
        select(HistorialSuscripcion.monto).where(HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .order_by(desc(HistorialSuscripcion.vigente_desde)).limit(1).scalar_subquery()
    )

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
        cast(Transaccion.tipo, String).label("extra_4") # tipo_transaccion
    ).join(Categoria, isouter=True).join(Billetera, isouter=True).where(
        and_(Transaccion.usuario_id == usuario.id, Transaccion.fecha >= fecha_inicio, Transaccion.fecha <= fecha_fin, Transaccion.es_padre_cuotas == False)
    ).order_by(desc(Transaccion.fecha), desc(Transaccion.fecha_creacion)).limit(6)

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
        cast(null(), String).label("extra_4")
    ).where(
        and_(Suscripcion.usuario_id == usuario.id, Suscripcion.estado == EstadoSuscripcion.ACTIVA, Suscripcion.proximo_cobro >= hoy, Suscripcion.proximo_cobro <= limite_pagos)
    )

    c_stmt = select(
        literal("cuota").label("item_tipo"),
        cast(Cuota.id, String).label("id"),
        GrupoCuotas.descripcion.label("nombre"),
        Cuota.monto_proyectado.label("monto"),
        cast(GrupoCuotas.moneda, String).label("moneda"),
        Cuota.fecha_vencimiento.label("fecha"),
        cast(null(), String).label("extra_1"),
        cast(null(), String).label("extra_2"),
        cast(null(), String).label("extra_3"),
        cast(null(), String).label("extra_4")
    ).join(GrupoCuotas).where(
        and_(GrupoCuotas.usuario_id == usuario.id, Cuota.pagada == False, Cuota.fecha_vencimiento >= hoy, Cuota.fecha_vencimiento <= limite_pagos)
    )

    actividad = db.execute(m_stmt.union_all(s_stmt, c_stmt)).all()

    # --- Procesamiento de Resultados ---
    ingresos, egresos = res.ing_actual or Decimal("0"), res.egr_actual or Decimal("0")
    ing_ant, egr_ant = res.ing_ant or Decimal("0"), res.egr_ant or Decimal("0")
    balance, balance_ant = ingresos - egresos, ing_ant - egr_ant
    total_b, cuotas_c = res.total_billeteras or Decimal("0"), res.cuotas_comprometidas or Decimal("0")
    
    variacion = round(float(((balance - balance_ant) / abs(balance_ant)) * 100), 1) if balance_ant != 0 else None

    movimientos_data = [{
        "id": r.id, "descripcion": r.nombre, "fecha": r.fecha.isoformat(), "monto": float(r.monto),
        "tipo": r.extra_4, "moneda": r.moneda, "billetera_nombre": r.extra_2 or "Billetera",
        "categoria_nombre": r.extra_1, "estado_verificacion": r.extra_3
    } for r in actividad if r.item_tipo == "movimiento"]

    proximos_pagos = sorted([{
        "id": r.id, "nombre": r.nombre, "monto": float(r.monto or 0), "moneda": r.moneda,
        "fecha_cobro": r.fecha.isoformat(), "dias_restantes": (r.fecha - hoy).days, "tipo": r.item_tipo
    } for r in actividad if r.item_tipo in ("suscripcion", "cuota")], key=lambda x: x["fecha_cobro"])[:5]

    return {
        "periodo": {
            "fecha_inicio": fecha_inicio.isoformat(), "fecha_fin": fecha_fin.isoformat(),
            "primera_transaccion": res.primera_tx.isoformat() if res.primera_tx else None
        },
        "balance": {
            "ingresos": float(ingresos), "egresos": float(egresos), "balance": float(balance),
            "variacion_vs_ciclo_anterior": variacion
        },
        "disponible_real": {
            "total_billeteras": float(total_b), "cuotas_comprometidas_proximo_ciclo": float(cuotas_c),
            "disponible": float(total_b - cuotas_c)
        },
        "ultimos_movimientos": movimientos_data,
        "proximos_pagos": proximos_pagos
    }

async def get_cotizacion_usuario(usuario: Usuario) -> Dict[str, Any]:
    tipo = usuario.tipo_dolar or "blue"
    url = f"https://dolarapi.com/v1/dolares/{tipo}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return {"tipo": data.get("casa", tipo), "compra": data.get("compra"), "venta": data.get("venta"), "fecha_actualizacion": data.get("fechaActualizacion")}
    except Exception:
        return {"tipo": tipo, "compra": 0, "venta": 0, "fecha_actualizacion": None, "error": "Servicio de cotizaciones no disponible"}

async def get_resumen_completo(db: Session, usuario: Usuario, desde: Optional[date] = None, hasta: Optional[date] = None) -> Dict[str, Any]:
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
    total_saldo_activa = Decimal("0")
    for b, has_tx in rows_billeteras:
        if b.estado == EstadoBilletera.ACTIVA:
            total_saldo_activa += Decimal(str(b.saldo_actual))
        billeteras_data.append({
            "id": str(b.id), "nombre": b.nombre, "moneda": b.moneda.value, "saldo_actual": float(b.saldo_actual),
            "es_principal": b.es_principal, "es_efectivo": b.es_efectivo, "estado": b.estado.value, "tiene_transacciones": has_tx
        })

    # QUERY 2 y 3: Se ejecutan dentro de get_dashboard_resumen
    resumen = get_dashboard_resumen(db, usuario, desde, hasta, total_billeteras_override=total_saldo_activa)
    cotizacion = await get_cotizacion_usuario(usuario)

    return {"billeteras": billeteras_data, "resumen": resumen, "cotizacion": cotizacion}
