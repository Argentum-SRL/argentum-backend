from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.transaccion import OrigenTransaccion, Transaccion
from app.models.usuario import Usuario
from app.services import dolar_service
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto, condicion_ingreso

MESES_CORTOS = {
    1: "Ene",
    2: "Feb",
    3: "Mar",
    4: "Abr",
    5: "May",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Sep",
    10: "Oct",
    11: "Nov",
    12: "Dic",
}

MESES_LARGOS = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}


def generar_ventana_ciclos(
    usuario: Usuario,
    hoy: date,
    cantidad_ciclos: int,
) -> List[Dict[str, Any]]:
    """
    Genera la lista cronológica de los últimos `cantidad_ciclos` ciclos financieros del usuario,
    desde el más antiguo hasta el ciclo actual, utilizando exclusivamente get_ciclo_fechas.
    """
    n = max(1, min(24, cantidad_ciclos))
    ciclos_reversa: List[Dict[str, Any]] = []
    cursor = hoy

    for idx in range(n):
        ini, fin = get_ciclo_fechas(usuario, cursor)
        dias_totales = (fin - ini).days + 1
        if fin < hoy:
            dias_transcurridos = dias_totales
        elif ini > hoy:
            dias_transcurridos = 0
        else:
            dias_transcurridos = min(dias_totales, max(1, (hoy - ini).days + 1))

        punto_medio = ini + (fin - ini) // 2
        mes_txt = MESES_CORTOS.get(punto_medio.month, str(punto_medio.month))
        anio_corto = str(punto_medio.year)[-2:]
        etiqueta = f"{mes_txt} {anio_corto}"
        rango_texto = f"{ini.strftime('%d/%m')} al {fin.strftime('%d/%m')}"

        ciclos_reversa.append({
            "indice_atras": idx,
            "clave": f"{punto_medio.year}-{punto_medio.month:02d}-{ini.isoformat()}",
            "etiqueta": etiqueta,
            "rango_texto": rango_texto,
            "fecha_inicio": ini,
            "fecha_fin": fin,
            "es_actual": (idx == 0),
            "dias_transcurridos": dias_transcurridos,
            "dias_totales": dias_totales,
        })
        cursor = ini - timedelta(days=1)

    return list(reversed(ciclos_reversa))


def obtener_mapa_cotizaciones(
    db: Session,
    modo: str,
    fechas: set[date],
) -> Dict[date, Optional[Decimal]]:
    """
    Devuelve las cotizaciones oficiales de venta para las fechas solicitadas.
    """
    if modo not in ("consolidado_ars", "consolidado_usd") or not fechas:
        return {}
    cots = dolar_service.obtener_cotizaciones_por_fechas(db, "oficial", fechas)
    res: Dict[date, Optional[Decimal]] = {}
    for f, c in cots.items():
        if c is not None and c.venta is not None and c.venta > 0:
            res[f] = Decimal(str(c.venta))
        else:
            res[f] = None
    return res


def convertir_movimiento(
    monto: Decimal,
    moneda_str: str,
    fecha: date,
    modo: str,
    cotizaciones: Dict[date, Optional[Decimal]],
) -> tuple[Optional[Decimal], bool]:
    """
    Convierte el monto de una transacción a la moneda visual requerida.
    Retorna (monto_convertido, falta_cotizacion).
    """
    mon = moneda_str.upper()
    if modo == "solo_ars":
        if mon == "ARS":
            return monto, False
        return None, False

    if modo == "solo_usd":
        if mon == "USD":
            return monto, False
        return None, False

    if modo == "consolidado_ars":
        if mon == "ARS":
            return monto, False
        tc = cotizaciones.get(fecha)
        if tc is None or tc <= 0:
            return None, True
        return monto * tc, False

    if modo == "consolidado_usd":
        if mon == "USD":
            return monto, False
        tc = cotizaciones.get(fecha)
        if tc is None or tc <= 0:
            return None, True
        return monto / tc, False

    return monto, False


def aplicar_filtros_extra(
    cond_base: Any,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    categoria_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
) -> Any:
    """
    Aplica filtros opcionales de billetera, tarjeta, categoría y naturaleza del gasto.
    """
    conds = [cond_base]
    if billetera_id:
        conds.append(Transaccion.billetera_id == billetera_id)
    if tarjeta_id:
        conds.append(Transaccion.tarjeta_id == tarjeta_id)
    if categoria_id:
        conds.append(Transaccion.categoria_id == categoria_id)

    nat = (naturaleza or "todos").lower().strip()
    if nat == "cuotas":
        conds.append(
            or_(
                Transaccion.es_cuota_hija.is_(True),
                Transaccion.grupo_cuotas_id.is_not(None),
            )
        )
    elif nat == "fijos":
        conds.append(
            or_(
                Transaccion.suscripcion_id.is_not(None),
                Transaccion.origen == OrigenTransaccion.RECURRENTE,
            )
        )
    elif nat == "variables":
        conds.append(Transaccion.es_cuota_hija.is_(False))
        conds.append(Transaccion.grupo_cuotas_id.is_(None))
        conds.append(Transaccion.suscripcion_id.is_(None))
        conds.append(Transaccion.origen != OrigenTransaccion.RECURRENTE)

    return and_(*conds)


def sumar_periodo(
    db: Session,
    usuario_id: UUID,
    desde: date,
    hasta: date,
    ref_hoy: date,
    modo: str,
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
    categoria_id: Optional[UUID],
    naturaleza: Optional[str],
    solo_gastos: bool,
) -> tuple[Decimal, Optional[Decimal]]:
    """
    Calcula los gastos e ingresos de un período acotado (para variaciones comparativas).
    """
    cond_g = aplicar_filtros_extra(
        condicion_gasto(usuario_id, desde=desde, hasta=hasta, hoy=ref_hoy),
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=categoria_id,
        naturaleza=naturaleza,
    )
    rows_g = db.execute(
        select(
            Transaccion.fecha,
            Transaccion.moneda,
            func.coalesce(func.sum(Transaccion.monto), 0),
        )
        .where(cond_g)
        .group_by(Transaccion.fecha, Transaccion.moneda)
    ).all()
    fechas_g = {r[0] for r in rows_g}
    cots_g = obtener_mapa_cotizaciones(db, modo, fechas_g)
    gasto_tot = Decimal("0")
    for f_tx, mon_tx, monto_tx in rows_g:
        m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
        m_conv, _ = convertir_movimiento(Decimal(str(monto_tx)), m_code, f_tx, modo, cots_g)
        if m_conv is not None:
            gasto_tot += m_conv

    ingreso_tot = None
    if not solo_gastos:
        ingreso_tot = Decimal("0")
        cond_i_base = condicion_ingreso(usuario_id, desde=desde, hasta=hasta, hoy=ref_hoy)
        cond_i = (
            and_(cond_i_base, Transaccion.billetera_id == billetera_id)
            if billetera_id
            else cond_i_base
        )
        rows_i = db.execute(
            select(
                Transaccion.fecha,
                Transaccion.moneda,
                func.coalesce(func.sum(Transaccion.monto), 0),
            )
            .where(cond_i)
            .group_by(Transaccion.fecha, Transaccion.moneda)
        ).all()
        fechas_i = {r[0] for r in rows_i}
        cots_i = obtener_mapa_cotizaciones(db, modo, fechas_i)
        for f_tx, mon_tx, monto_tx in rows_i:
            m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
            m_conv, _ = convertir_movimiento(Decimal(str(monto_tx)), m_code, f_tx, modo, cots_i)
            if m_conv is not None:
                ingreso_tot += m_conv

    return gasto_tot, ingreso_tot


def calcular_serie_ciclos(
    ciclos: List[Dict[str, Any]],
    ref_hoy: date,
    modo: str,
    solo_gastos: bool,
    rows_g_diarios: List[Any],
    rows_i_diarios: List[Any],
    cotizaciones_map: Dict[date, Optional[Decimal]],
) -> tuple[List[Dict[str, Any]], int, int]:
    serie_ciclos_raw: List[Dict[str, Any]] = []
    movimientos_sin_cotizacion = 0
    total_operaciones_gasto = 0

    for c in ciclos:
        c_ini: date = c["fecha_inicio"]
        c_fin: date = min(c["fecha_fin"], ref_hoy)
        por_cat_dec: Dict[str, Decimal] = {}
        por_sub_dec: Dict[str, Decimal] = {}
        gasto_ciclo = Decimal("0")
        ingreso_ciclo = Decimal("0") if not solo_gastos else None

        if c_ini <= ref_hoy:
            for f_tx, mon_tx, cat_tx, sub_tx, monto_tx, cant_tx in rows_g_diarios:
                if c_ini <= f_tx <= c_fin:
                    m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
                    m_dec = Decimal(str(monto_tx))
                    m_conv, sin_cot = convertir_movimiento(m_dec, m_code, f_tx, modo, cotizaciones_map)
                    if sin_cot:
                        movimientos_sin_cotizacion += int(cant_tx)
                        continue
                    if m_conv is None:
                        continue

                    gasto_ciclo += m_conv
                    total_operaciones_gasto += int(cant_tx)
                    cid_key = str(cat_tx) if cat_tx else "sin-categoria"
                    por_cat_dec[cid_key] = por_cat_dec.get(cid_key, Decimal("0")) + m_conv
                    sid_key = str(sub_tx) if sub_tx else "general"
                    por_sub_dec[sid_key] = por_sub_dec.get(sid_key, Decimal("0")) + m_conv
                    por_sub_dec[f"{cid_key}:{sid_key}"] = (
                        por_sub_dec.get(f"{cid_key}:{sid_key}", Decimal("0")) + m_conv
                    )

            if not solo_gastos:
                for f_tx, mon_tx, monto_tx, cant_tx in rows_i_diarios:
                    if c_ini <= f_tx <= c_fin:
                        m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
                        m_dec = Decimal(str(monto_tx))
                        m_conv, sin_cot = convertir_movimiento(m_dec, m_code, f_tx, modo, cotizaciones_map)
                        if sin_cot:
                            movimientos_sin_cotizacion += int(cant_tx)
                            continue
                        if m_conv is None:
                            continue
                        assert ingreso_ciclo is not None
                        ingreso_ciclo += m_conv

        if solo_gastos or ingreso_ciclo is None:
            balance_ciclo = None
            tasa_ahorro = None
        else:
            balance_ciclo = ingreso_ciclo - gasto_ciclo
            if ingreso_ciclo <= 0 or balance_ciclo < -ingreso_ciclo:
                tasa_ahorro = None
            else:
                tasa_ahorro = round(float((balance_ciclo / ingreso_ciclo) * Decimal("100")), 1)

        serie_ciclos_raw.append({
            "clave": c["clave"],
            "etiqueta": c["etiqueta"],
            "rango_texto": c["rango_texto"],
            "fecha_inicio": c["fecha_inicio"].isoformat(),
            "fecha_fin": c["fecha_fin"].isoformat(),
            "es_actual": c["es_actual"],
            "dias_transcurridos": c["dias_transcurridos"],
            "dias_totales": c["dias_totales"],
            "ingresos": round(float(ingreso_ciclo), 2) if ingreso_ciclo is not None else None,
            "gastos": round(float(gasto_ciclo), 2),
            "balance": round(float(balance_ciclo), 2) if balance_ciclo is not None else None,
            "tasa_ahorro_pct": tasa_ahorro,
            "por_categoria": {k: round(float(v), 2) for k, v in por_cat_dec.items()},
            "por_subcategoria": {k: round(float(v), 2) for k, v in por_sub_dec.items()},
            "_gasto_dec": gasto_ciclo,
            "_ingreso_dec": ingreso_ciclo,
        })

    return serie_ciclos_raw, movimientos_sin_cotizacion, total_operaciones_gasto


def calcular_promedios_y_tasa(
    serie_ciclos_raw: List[Dict[str, Any]],
    ref_hoy: date,
    solo_gastos: bool,
) -> tuple[List[Dict[str, Any]], Optional[float], Optional[float], Optional[float], List[Dict[str, Any]]]:
    ciclos_completos = [
        item for item in serie_ciclos_raw if date.fromisoformat(item["fecha_fin"]) < ref_hoy
    ]
    if not ciclos_completos:
        promedio_gasto_ciclo: Optional[float] = None
        promedio_ingreso_ciclo: Optional[float] = None
        tasa_ahorro_ventana: Optional[float] = None
    else:
        cant_comp = len(ciclos_completos)
        sum_g_comp = sum(item["_gasto_dec"] for item in ciclos_completos)
        promedio_gasto_ciclo = round(float(sum_g_comp / Decimal(str(cant_comp))), 2)

        if solo_gastos:
            promedio_ingreso_ciclo = None
            tasa_ahorro_ventana = None
        else:
            sum_i_comp = sum(
                (item["_ingreso_dec"] for item in ciclos_completos if item["_ingreso_dec"] is not None),
                Decimal("0"),
            )
            promedio_ingreso_ciclo = round(float(sum_i_comp / Decimal(str(cant_comp))), 2)
            bal_comp = sum_i_comp - sum_g_comp
            if sum_i_comp <= 0 or bal_comp < -sum_i_comp:
                tasa_ahorro_ventana = None
            else:
                tasa_ahorro_ventana = round(float((bal_comp / sum_i_comp) * Decimal("100")), 1)

    serie_ciclos = [
        {
            k: v
            for k, v in {
                **item,
                "promedio_historico_gastos": promedio_gasto_ciclo,
            }.items()
            if not k.startswith("_")
        }
        for item in serie_ciclos_raw
    ]
    return ciclos_completos, promedio_gasto_ciclo, promedio_ingreso_ciclo, tasa_ahorro_ventana, serie_ciclos
