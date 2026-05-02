from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from fastapi import HTTPException
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, select, desc, or_
from sqlalchemy.orm import Session, joinedload

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
    """
    Calcula la fecha exacta segun una regla (ej: ultimo_viernes, primer_lunes).
    """
    parts = rule.lower().split("_")
    if len(parts) != 2:
        return date(year, month, 1)
    
    when = parts[0]
    weekday_str = parts[1]
    
    weekdays = {
        "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
        "viernes": 4, "sabado": 5, "domingo": 6
    }
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
    """
    Calcula fecha_inicio y fecha_fin del ciclo actual del usuario.
    """
    if not usuario.ciclo_tipo or not usuario.ciclo_valor:
        inicio = hoy.replace(day=1)
        fin = (inicio + relativedelta(months=1)) - timedelta(days=1)
        return inicio, fin

    if usuario.ciclo_tipo == CicloTipo.DIA_FIJO:
        try:
            dia = int(usuario.ciclo_valor)
        except ValueError:
            dia = 1
        
        # Ajustar si el dia es mayor a los dias del mes
        last_of_month = (hoy.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
        dia_ajustado = min(dia, last_of_month.day)
        
        if hoy.day >= dia_ajustado:
            inicio = hoy.replace(day=dia_ajustado)
        else:
            # Mes anterior
            prev_month = hoy - relativedelta(months=1)
            last_of_prev = (prev_month.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
            dia_ajustado_prev = min(dia, last_of_prev.day)
            inicio = prev_month.replace(day=dia_ajustado_prev)
        
        # El fin es el dia anterior al inicio del proximo ciclo
        proximo_inicio = inicio + relativedelta(months=1)
        # Re-ajustar proximo_inicio dia
        last_of_next = (proximo_inicio.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
        proximo_inicio = proximo_inicio.replace(day=min(dia, last_of_next.day))
        
        fin = proximo_inicio - timedelta(days=1)
        return inicio, fin

    if usuario.ciclo_tipo == CicloTipo.REGLA:
        # Calcular regla para este mes
        d_regla = get_date_by_rule(usuario.ciclo_valor, hoy.month, hoy.year)
        
        if hoy >= d_regla:
            inicio = d_regla
        else:
            # Regla del mes anterior
            prev = hoy - relativedelta(months=1)
            inicio = get_date_by_rule(usuario.ciclo_valor, prev.month, prev.year)
            
        # Fin: regla del mes siguiente - 1 dia
        prox = inicio + relativedelta(months=1)
        fin = get_date_by_rule(usuario.ciclo_valor, prox.month, prox.year) - timedelta(days=1)
        return inicio, fin

    # Fallback final
    inicio = hoy.replace(day=1)
    fin = (inicio + relativedelta(months=1)) - timedelta(days=1)
    return inicio, fin

def get_dashboard_resumen(
    db: Session, 
    usuario: Usuario, 
    fecha_desde_override: Optional[date] = None, 
    fecha_hasta_override: Optional[date] = None
) -> Dict[str, Any]:
    # Argentum usa UTC-3 para presentacion, pero las fechas en DB son Date (sin timezone)
    # o DateTime (con timezone). Transaccion.fecha es Date.
    # Usamos la fecha local de "hoy" en Argentina (UTC-3).
    hoy = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    
    if fecha_desde_override and fecha_hasta_override:
        fecha_inicio, fecha_fin = fecha_desde_override, fecha_hasta_override
    else:
        fecha_inicio, fecha_fin = get_ciclo_fechas(usuario, hoy)
    
    # Obtener fecha de la primera transaccion para el limite inferior del frontend
    primera_tx_fecha = db.execute(
        select(func.min(Transaccion.fecha))
        .where(Transaccion.usuario_id == usuario.id)
    ).scalar()
    
    # Ciclo anterior
    # Para obtener el ciclo anterior, tomamos un dia antes del inicio del actual
    fecha_inicio_ant, fecha_fin_ant = get_ciclo_fechas(usuario, fecha_inicio - timedelta(days=1))

    # Balance actual
    transacciones_ciclo = db.execute(
        select(Transaccion)
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio,
                Transaccion.fecha <= fecha_fin,
                Transaccion.es_padre_cuotas == False,
                or_(
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                    Transaccion.estado_verificacion == None
                )
            )
        )
    ).scalars().all()
    
    ingresos = sum(t.monto for t in transacciones_ciclo if t.tipo == TipoTransaccion.INGRESO)
    egresos = sum(t.monto for t in transacciones_ciclo if t.tipo == TipoTransaccion.EGRESO)
    balance = ingresos - egresos
    
    # Balance anterior
    transacciones_ant = db.execute(
        select(Transaccion)
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio_ant,
                Transaccion.fecha <= fecha_fin_ant,
                Transaccion.es_padre_cuotas == False,
                or_(
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                    Transaccion.estado_verificacion == None
                )
            )
        )
    ).scalars().all()
    
    ingresos_ant = sum(t.monto for t in transacciones_ant if t.tipo == TipoTransaccion.INGRESO)
    egresos_ant = sum(t.monto for t in transacciones_ant if t.tipo == TipoTransaccion.EGRESO)
    balance_ant = ingresos_ant - egresos_ant
    
    variacion = None
    if balance_ant != 0:
        variacion = round(float(((balance - balance_ant) / abs(balance_ant)) * 100), 1)

    # Disponible real
    billeteras = db.execute(
        select(Billetera)
        .where(
            and_(
                Billetera.usuario_id == usuario.id,
                Billetera.estado == EstadoBilletera.ACTIVA
            )
        )
    ).scalars().all()
    total_billeteras = sum(b.saldo_actual for b in billeteras)
    
    # Proximo ciclo para cuotas comprometidas
    fecha_inicio_prox, fecha_fin_prox = get_ciclo_fechas(usuario, fecha_fin + timedelta(days=1))
    
    cuotas_comprometidas = db.execute(
        select(func.sum(Cuota.monto_proyectado))
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(
            and_(
                GrupoCuotas.usuario_id == usuario.id,
                Cuota.pagada == False,
                Cuota.fecha_vencimiento >= fecha_inicio_prox,
                Cuota.fecha_vencimiento <= fecha_fin_prox
            )
        )
    ).scalar() or Decimal("0")
    
    disponible = total_billeteras - cuotas_comprometidas
    
    # Ultimos movimientos (del ciclo actual)
    ultimos_movimientos = db.execute(
        select(Transaccion)
        .options(joinedload(Transaccion.billetera), joinedload(Transaccion.categoria))
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio,
                Transaccion.fecha <= fecha_fin,
                Transaccion.es_padre_cuotas == False
            )
        )
        .order_by(desc(Transaccion.fecha), desc(Transaccion.fecha_creacion))
        .limit(6)
    ).scalars().all()
    
    movimientos_data = []
    for m in ultimos_movimientos:
        movimientos_data.append({
            "id": str(m.id),
            "descripcion": m.descripcion,
            "fecha": m.fecha.isoformat(),
            "monto": float(m.monto),
            "tipo": m.tipo.value,
            "moneda": m.moneda.value,
            "billetera_nombre": m.billetera.nombre if m.billetera else "Billetera",
            "categoria_nombre": m.categoria.nombre if m.categoria else None,
            "estado_verificacion": m.estado_verificacion.value if m.estado_verificacion else None
        })

    # Proximos pagos (30 dias)
    limite_pagos = hoy + timedelta(days=30)
    
    # Suscripciones
    suscripciones = db.execute(
        select(Suscripcion)
        .options(joinedload(Suscripcion.historial))
        .where(
            and_(
                Suscripcion.usuario_id == usuario.id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
                Suscripcion.proximo_cobro >= hoy,
                Suscripcion.proximo_cobro <= limite_pagos
            )
        )
    ).scalars().unique().all()
    
    # Cuotas
    cuotas = db.execute(
        select(Cuota)
        .options(joinedload(Cuota.grupo))
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(
            and_(
                GrupoCuotas.usuario_id == usuario.id,
                Cuota.pagada == False,
                Cuota.fecha_vencimiento >= hoy,
                Cuota.fecha_vencimiento <= limite_pagos
            )
        )
    ).scalars().all()
    
    proximos_pagos = []
    for s in suscripciones:
        monto = Decimal("0")
        if s.historial:
            ultimo = sorted(s.historial, key=lambda h: h.fecha_desde, reverse=True)[0]
            monto = ultimo.monto
            
        proximos_pagos.append({
            "id": str(s.id),
            "nombre": s.nombre,
            "monto": float(monto),
            "moneda": usuario.moneda_principal.value if usuario.moneda_principal else "ARS",
            "fecha_cobro": s.proximo_cobro.isoformat(),
            "dias_restantes": (s.proximo_cobro - hoy).days,
            "tipo": "suscripcion"
        })
        
    for c in cuotas:
        proximos_pagos.append({
            "id": str(c.id),
            "nombre": f"{c.grupo.descripcion}",
            "monto": float(c.monto_proyectado),
            "moneda": c.grupo.moneda.value,
            "fecha_cobro": c.fecha_vencimiento.isoformat(),
            "dias_restantes": (c.fecha_vencimiento - hoy).days,
            "tipo": "cuota"
        })
        
    proximos_pagos.sort(key=lambda x: x["fecha_cobro"])
    proximos_pagos = proximos_pagos[:5]

    return {
        "periodo": {
            "fecha_inicio": fecha_inicio.isoformat(),
            "fecha_fin": fecha_fin.isoformat(),
            "primera_transaccion": primera_tx_fecha.isoformat() if primera_tx_fecha else None
        },
        "balance": {
            "ingresos": float(ingresos),
            "egresos": float(egresos),
            "balance": float(balance),
            "variacion_vs_ciclo_anterior": variacion
        },
        "disponible_real": {
            "total_billeteras": float(total_billeteras),
            "cuotas_comprometidas_proximo_ciclo": float(cuotas_comprometidas),
            "disponible": float(disponible)
        },
        "ultimos_movimientos": movimientos_data,
        "proximos_pagos": proximos_pagos
    }

def get_cotizacion_usuario(usuario: Usuario) -> Dict[str, Any]:
    tipo = usuario.tipo_dolar or "blue"
    url = f"https://dolarapi.com/v1/dolares/{tipo}"
    
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
            return {
                "tipo": data.get("casa", tipo),
                "compra": data.get("compra"),
                "venta": data.get("venta"),
                "fecha_actualizacion": data.get("fechaActualizacion")
            }
    except Exception:
        raise HTTPException(status_code=503, detail="Servicio de cotizaciones no disponible")
