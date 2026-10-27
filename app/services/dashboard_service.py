from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from dateutil.relativedelta import relativedelta
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.utils.fecha import hoy_argentina
from app.models.usuario import Usuario, CicloTipo, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import Transaccion
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.services import pagos_proximos_service

logger = logging.getLogger(__name__)

def get_date_by_rule(rule: str, month: int, year: int) -> date:
    """Calcula la fecha exacta segun una regla (ej: ultimo_viernes, ultimo_dia_habil, dia_habil_4)."""
    from app.services.dias_habiles_service import _get_feriados_cached_sync, es_dia_habil

    rule_lower = rule.lower()
    feriados = _get_feriados_cached_sync(year)
    num_days = calendar.monthrange(year, month)[1]

    if rule_lower == "ultimo_dia_habil":
        for day in range(num_days, 0, -1):
            d = date(year, month, day)
            if es_dia_habil(d, feriados):
                return d
        return date(year, month, num_days)

    if rule_lower == "primer_dia_habil" or rule_lower.startswith("dia_habil_"):
        if rule_lower == "primer_dia_habil":
            target_n = 1
        else:
            try:
                target_n = int(rule_lower.split("_")[-1])
            except ValueError:
                # Se ignora: si el sufijo de la regla no es un número válido, se usa el primer día hábil.
                target_n = 1

        count = 0
        last_found = None
        for day in range(1, num_days + 1):
            d = date(year, month, day)
            if es_dia_habil(d, feriados):
                count += 1
                last_found = d
                if count == target_n:
                    return d
        return last_found or date(year, month, 1)

    if rule_lower == "ultimo_viernes":
        # Regla semanal mantenida: ultimo viernes del mes (4 = viernes)
        last_day = date(year, month, num_days)
        d = last_day
        while d.weekday() != 4:
            d -= timedelta(days=1)
        return d

    # Respaldo seguro para cualquier otra regla no reconocida: mes calendario (primer dia del mes)
    return date(year, month, 1)

def calcular_inicio_ciclo_para_mes_ancla(usuario: Usuario, anio: int, mes: int) -> date:
    """
    Calcula la fecha de inicio del ciclo correspondiente al mes ancla (anio, mes).
    Nunca usa una fecha previamente ajustada para derivar año o mes.
    """
    ciclo_dir = getattr(usuario, "ciclo_ajuste_direccion", None)
    direccion = (
        ciclo_dir.value if hasattr(ciclo_dir, "value")
        else (str(ciclo_dir) if ciclo_dir else None)
    )

    if usuario.ciclo_tipo == CicloTipo.DIA_FIJO:
        from app.services.dias_habiles_service import ajustar_fecha_habil_sync
        try:
            dia = int(usuario.ciclo_valor)
        except (ValueError, TypeError):
            # Se ignora: si ciclo_valor no es convertible a entero, se usa el día 1 como respaldo.
            dia = 1
        
        ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
        dia_real = min(dia, ultimo_dia_mes)
        fecha_nominal = date(anio, mes, dia_real)
        
        if direccion:
            return ajustar_fecha_habil_sync(fecha_nominal, direccion=direccion)
        return fecha_nominal

    elif usuario.ciclo_tipo == CicloTipo.REGLA:
        from app.services.dias_habiles_service import ajustar_fecha_habil_sync
        val = (usuario.ciclo_valor or "").lower()
        if not val:
            # Respaldo de mes calendario si la regla no esta configurada (mismo que usuarios sin ciclo)
            return date(anio, mes, 1)
        fecha_nominal = get_date_by_rule(val, mes, anio)
        if val in ("ultimo_dia_habil", "primer_dia_habil") or val.startswith("dia_habil_"):
            return fecha_nominal
        if direccion:
            return ajustar_fecha_habil_sync(fecha_nominal, direccion=direccion)
        return fecha_nominal

    else:
        # Default mes calendario
        return date(anio, mes, 1)


def get_ciclo_fechas(usuario: Usuario, hoy: date) -> tuple[date, date]:
    """
    Calcula fecha_inicio y fecha_fin del ciclo al que pertenece 'hoy'.
    Basado en el modelo conceptual de Mes Ancla para garantizar rangos válidos,
    sin huecos, superposiciones ni fechas invertidas.
    """
    if not usuario.ciclo_tipo or not usuario.ciclo_valor:
        inicio = hoy.replace(day=1)
        fin = (inicio + relativedelta(months=1)) - timedelta(days=1)
        return inicio, fin

    # Determinamos los meses ancla candidatos alrededor de 'hoy'
    # Evaluamos en orden de tiempo: mes + 1, mes 0, mes - 1, mes - 2
    candidatos = [
        hoy + relativedelta(months=1),
        hoy,
        hoy - relativedelta(months=1),
        hoy - relativedelta(months=2),
    ]
    
    # Encontramos el mes ancla M tal que inicio(M) <= hoy < inicio(M+1)
    for i in range(len(candidatos) - 1):
        m_curr = candidatos[i + 1]
        m_next = candidatos[i]
        ini_curr = calcular_inicio_ciclo_para_mes_ancla(usuario, m_curr.year, m_curr.month)
        ini_next = calcular_inicio_ciclo_para_mes_ancla(usuario, m_next.year, m_next.month)
        
        if ini_curr <= hoy < ini_next:
            return ini_curr, ini_next - timedelta(days=1)

    # Fallback si hoy >= inicio(hoy + 1 mes)
    m_top = candidatos[0]
    m_top_next = m_top + relativedelta(months=1)
    ini_top = calcular_inicio_ciclo_para_mes_ancla(usuario, m_top.year, m_top.month)
    ini_top_next = calcular_inicio_ciclo_para_mes_ancla(usuario, m_top_next.year, m_top_next.month)
    if hoy >= ini_top:
        return ini_top, ini_top_next - timedelta(days=1)
        
    # Fallback general
    m_bot = candidatos[-1]
    m_bot_next = candidatos[-2]
    ini_bot = calcular_inicio_ciclo_para_mes_ancla(usuario, m_bot.year, m_bot.month)
    ini_bot_next = calcular_inicio_ciclo_para_mes_ancla(usuario, m_bot_next.year, m_bot_next.month)
    return ini_bot, ini_bot_next - timedelta(days=1)


def get_dashboard_resumen(
    db: Session, 
    usuario: Usuario, 
    fecha_desde_override: Optional[date] = None, 
    fecha_hasta_override: Optional[date] = None,
    total_billeteras_override: Optional[Dict[str, Decimal]] = None,
    billetera_ids: Optional[List[UUID]] = None,
    hoy: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Retorna el resumen optimizado del dashboard en máximo 2 queries DB.
    """
    ref_hoy = hoy or hoy_argentina()
    hoy = ref_hoy
    fecha_inicio, fecha_fin = (fecha_desde_override, fecha_hasta_override) if (fecha_desde_override and fecha_hasta_override) else get_ciclo_fechas(usuario, hoy)
    fecha_inicio_ant, fecha_fin_ant = get_ciclo_fechas(usuario, fecha_inicio - timedelta(days=1))
    fecha_inicio_prox, fecha_fin_prox = get_ciclo_fechas(usuario, fecha_fin + timedelta(days=1))

    # --- QUERY 1: Balances, Totales y Estadísticas Globales ---
    primera_tx = db.execute(select(func.min(Transaccion.fecha)).where(Transaccion.usuario_id == usuario.id)).scalar()
    balance_res = calcular_balance_ciclo(db, usuario, fecha_desde_override=fecha_inicio, fecha_hasta_override=fecha_fin, billetera_ids=billetera_ids)

    # --- QUERY 2: Últimos movimientos del ciclo/período (vía listar_movimientos) ---
    from app.services.historial_service import listar_movimientos
    resp_movs = listar_movimientos(
        db=db,
        usuario_id=usuario.id,
        modo="general",
        billetera_ids=billetera_ids,
        fecha_desde=fecha_inicio,
        fecha_hasta=hoy,
        skip=0,
        limit=12,
        hoy=hoy,
    )

    cats_rows = db.execute(select(Categoria.id, Categoria.nombre)).all()
    cat_names_map = {c[0]: c[1] for c in cats_rows}

    movimientos_data = []
    for item in resp_movs.items:
        tx = item.transaccion
        # D8: Los espejos no se listan en Últimos movimientos
        if item.etiqueta == "Reintegro" or (tx is not None and getattr(tx, "reintegro_de_id", None) is not None):
            continue

        if item.clase == "transaccion" and tx is not None:
            tipo_item = tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo)
            moneda_item = tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda)
        else:
            tipo_item = item.clase
            moneda_item = item.moneda

        cat_nom = None
        subcat_nom = None
        estado_verif = None
        mov_meta_id = None

        if tx is not None:
            cat_nom = cat_names_map.get(tx.categoria_id)
            if tx.subcategoria:
                subcat_nom = tx.subcategoria.nombre
            if tx.estado_verificacion:
                estado_verif = (
                    tx.estado_verificacion.value
                    if hasattr(tx.estado_verificacion, "value")
                    else str(tx.estado_verificacion)
                )
            if tx.movimiento_meta_id:
                mov_meta_id = str(tx.movimiento_meta_id)

        reintegro_val = item.reintegro or (tx.reintegro if tx else None)

        movimientos_data.append({
            "id": item.id,
            "descripcion": item.descripcion,
            "fecha": item.fecha.isoformat(),
            "monto": float(item.monto),
            "tipo": tipo_item,
            "moneda": moneda_item,
            "sentido": item.sentido,
            "cuenta_como": item.cuenta_como,
            "billetera_nombre": item.billetera_nombre or "Billetera",
            "categoria_nombre": cat_nom,
            "estado_verificacion": estado_verif,
            "subcategoria_nombre": subcat_nom,
            "movimiento_meta_id": mov_meta_id,
            "etiqueta": item.etiqueta,
            "aclaracion": item.aclaracion,
            "cuota_texto": item.cuota_texto,
            "reintegro": reintegro_val,
        })
        if len(movimientos_data) == 6:
            break


    # --- Próximos Pagos y Disponible Libre (Ciclo actual canónico del usuario) ---
    fecha_inicio_ciclo_act, fecha_fin_ciclo_act = get_ciclo_fechas(usuario, hoy)

    proximos_pagos_completos = pagos_proximos_service.listar_pagos_proximos(
        db=db,
        usuario=usuario,
        fecha_fin_ciclo=fecha_fin_ciclo_act,
        hoy=hoy,
        billetera_ids=billetera_ids,
    )

    saldo_disp_actual = pagos_proximos_service.calcular_disponible_libre(
        db=db,
        usuario=usuario,
        fecha_fin_ciclo=fecha_fin_ciclo_act,
        hoy=hoy,
        billetera_ids=billetera_ids,
        total_billeteras_override=total_billeteras_override,
        pagos=proximos_pagos_completos,
    )

    # Recortar a 5 ítems por moneda para la card de Próximos pagos
    pagos_ars = sorted(
        [p for p in proximos_pagos_completos if p.get("moneda") == "ARS"],
        key=lambda x: (0 if x["dias_restantes"] < 0 else 1, x["fecha_cobro"])
    )[:5]
    pagos_usd = sorted(
        [p for p in proximos_pagos_completos if p.get("moneda") == "USD"],
        key=lambda x: (0 if x["dias_restantes"] < 0 else 1, x["fecha_cobro"])
    )[:5]
    pagos_otros = sorted(
        [p for p in proximos_pagos_completos if p.get("moneda") not in ("ARS", "USD")],
        key=lambda x: (0 if x["dias_restantes"] < 0 else 1, x["fecha_cobro"])
    )[:5]
    proximos_pagos = sorted(
        pagos_ars + pagos_usd + pagos_otros,
        key=lambda x: (0 if x["dias_restantes"] < 0 else 1, x["fecha_cobro"])
    )

    # --- QUERY: Gastos Reales por Categoría en el Ciclo Actual ---
    cat_where = condicion_gasto(
        usuario_id=usuario.id,
        desde=fecha_inicio,
        hasta=min(fecha_fin, hoy),
        hoy=hoy,
    )
    if billetera_ids:
        cat_where = and_(cat_where, Transaccion.billetera_id.in_(billetera_ids))

    cat_stmt = (
        select(
            Transaccion.categoria_id,
            Categoria.nombre.label("categoria_nombre"),
            Transaccion.moneda,
            func.sum(Transaccion.monto).label("total")
        )
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .where(cat_where)
        .group_by(Transaccion.categoria_id, Categoria.nombre, Transaccion.moneda)
    )
    cat_rows = db.execute(cat_stmt).all()

    gastos_cat_ars: List[Dict[str, Any]] = []
    gastos_cat_usd: List[Dict[str, Any]] = []
    for r in cat_rows:
        nombre = r.categoria_nombre or "General"
        cid = str(r.categoria_id) if r.categoria_id else None
        monto_float = float(r.total or 0)
        item = {
            "categoria_id": cid,
            "categoria_nombre": nombre,
            "monto": monto_float
        }
        if r.moneda == Moneda.ARS:
            gastos_cat_ars.append(item)
        elif r.moneda == Moneda.USD:
            gastos_cat_usd.append(item)

    gastos_cat_ars.sort(key=lambda x: -x["monto"])
    gastos_cat_usd.sort(key=lambda x: -x["monto"])

    return {
        "periodo": {
            "fecha_inicio": fecha_inicio.isoformat(), "fecha_fin": fecha_fin.isoformat(),
            "primera_transaccion": primera_tx.isoformat() if primera_tx else None
        },
        "balance": balance_res,
        "disponible_real": {
            "ars": {
                "saldo_billeteras": float(saldo_disp_actual["ars"]["saldo_total"]),
                "cuotas_proximo_ciclo": float(saldo_disp_actual["ars"]["cuotas_pendientes"]),
                "suscripciones_mensuales": float(saldo_disp_actual["ars"]["suscripciones_pendientes"]),
                "disponible": float(saldo_disp_actual["ars"]["saldo_disponible"])
            },
            "usd": {
                "saldo_billeteras": float(saldo_disp_actual["usd"]["saldo_total"]),
                "cuotas_proximo_ciclo": float(saldo_disp_actual["usd"]["cuotas_pendientes"]),
                "suscripciones_mensuales": float(saldo_disp_actual["usd"]["suscripciones_pendientes"]),
                "disponible": float(saldo_disp_actual["usd"]["saldo_disponible"])
            }
        },
        "saldo_disponible": saldo_disp_actual,
        "gastos_por_categoria": {
            "ars": gastos_cat_ars,
            "usd": gastos_cat_usd
        },
        "ultimos_movimientos": movimientos_data,
        "proximos_pagos": proximos_pagos
    }


def calcular_balance_ciclo(
    db: Session, 
    usuario: Usuario, 
    fecha_desde_override: Optional[date] = None, 
    fecha_hasta_override: Optional[date] = None,
    total_billeteras_override: Optional[Dict[str, Decimal]] = None,
    billetera_ids: Optional[List[UUID]] = None
) -> Dict[str, Any]:
    hoy = hoy_argentina()
    fecha_inicio, fecha_fin = (fecha_desde_override, fecha_hasta_override) if (fecha_desde_override and fecha_hasta_override) else get_ciclo_fechas(usuario, hoy)
    fecha_inicio_ant, fecha_fin_ant = get_ciclo_fechas(usuario, fecha_inicio - timedelta(days=1))
    hasta_act = min(fecha_fin, hoy)
    hasta_ant = min(fecha_fin_ant, hoy)

    cond_g_act = [condicion_gasto(usuario.id, desde=fecha_inicio, hasta=hasta_act, hoy=hoy)]
    cond_i_act = [condicion_ingreso(usuario.id, desde=fecha_inicio, hasta=hasta_act, hoy=hoy)]
    cond_g_ant = [condicion_gasto(usuario.id, desde=fecha_inicio_ant, hasta=hasta_ant, hoy=hoy)]
    cond_i_ant = [condicion_ingreso(usuario.id, desde=fecha_inicio_ant, hasta=hasta_ant, hoy=hoy)]

    if billetera_ids:
        cond_g_act.append(Transaccion.billetera_id.in_(billetera_ids))
        cond_i_act.append(Transaccion.billetera_id.in_(billetera_ids))
        cond_g_ant.append(Transaccion.billetera_id.in_(billetera_ids))
        cond_i_ant.append(Transaccion.billetera_id.in_(billetera_ids))

    def _sumas(conds):
        rows = db.execute(
            select(Transaccion.moneda, func.coalesce(func.sum(Transaccion.monto), 0))
            .where(and_(*conds))
            .group_by(Transaccion.moneda)
        ).all()
        return {r[0]: Decimal(str(r[1])) for r in rows}

    g_act = _sumas(cond_g_act)
    i_act = _sumas(cond_i_act)
    g_ant = _sumas(cond_g_ant)
    i_ant = _sumas(cond_i_ant)

    egr_actual_ars = g_act.get(Moneda.ARS, g_act.get("ARS", Decimal("0")))
    ing_actual_ars = i_act.get(Moneda.ARS, i_act.get("ARS", Decimal("0")))
    egr_ant_ars = g_ant.get(Moneda.ARS, g_ant.get("ARS", Decimal("0")))
    ing_ant_ars = i_ant.get(Moneda.ARS, i_ant.get("ARS", Decimal("0")))

    egr_actual_usd = g_act.get(Moneda.USD, g_act.get("USD", Decimal("0")))
    ing_actual_usd = i_act.get(Moneda.USD, i_act.get("USD", Decimal("0")))
    egr_ant_usd = g_ant.get(Moneda.USD, g_ant.get("USD", Decimal("0")))
    ing_ant_usd = i_ant.get(Moneda.USD, i_ant.get("USD", Decimal("0")))

    balance_ars = ing_actual_ars - egr_actual_ars
    balance_ant_ars = ing_ant_ars - egr_ant_ars
    variacion_ars = round(float(((balance_ars - balance_ant_ars) / abs(balance_ant_ars)) * 100), 1) if balance_ant_ars != 0 else None

    balance_usd = ing_actual_usd - egr_actual_usd
    balance_ant_usd = ing_ant_usd - egr_ant_usd
    variacion_usd = round(float(((balance_usd - balance_ant_usd) / abs(balance_ant_usd)) * 100), 1) if balance_ant_usd != 0 else None

    return {
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
        logger.warning("No se pudo obtener la cotización del dólar para el usuario", exc_info=True)

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
        if b.estado == EstadoBilletera.ACTIVA and not b.es_inversion:
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
            "es_inversion": bool(getattr(b, "es_inversion", False)),
            "estado": b.estado.value,
            "fecha_creacion": b.fecha_creacion.isoformat() if getattr(b, "fecha_creacion", None) else None,
            "bank_id": getattr(b, "bank_id", None),
            "tiene_transacciones": bool(has_tx)
        })

    # QUERY 2 y 3: Se ejecutan dentro de get_dashboard_resumen
    resumen = get_dashboard_resumen(db, usuario, desde, hasta, total_billeteras_override=total_saldo_activa, billetera_ids=billetera_ids)
    cotizacion = get_cotizacion_usuario(usuario)

    return {
        "billeteras": billeteras_data,
        "resumen": resumen,
        "cotizacion": cotizacion,
        "saldo_disponible": resumen.get("saldo_disponible"),
    }

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
        condicion_gasto(usuario.id, desde=fecha_inicio, hasta=min(fecha_fin, hoy), hoy=hoy),
        Transaccion.categoria_id == cat_uuid
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
            "subcategoria_nombre": "Otros",
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
