from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.usuario import Usuario
from app.services import dolar_service
from app.services.analisis_ciclos_service import MESES_CORTOS
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.cuotas import monto_efectivo_cuota
from app.utils.fecha import hoy_argentina


def _sumar_meses_calendario(anio: int, mes: int, delta_meses: int) -> tuple[int, int]:
    total = (anio * 12 + (mes - 1)) + delta_meses
    return total // 12, (total % 12) + 1


def _salto_meses_frecuencia(freq: FrecuenciaSuscripcion | str) -> int:
    val = freq.value if isinstance(freq, FrecuenciaSuscripcion) else str(freq)
    mapa = {
        "mensual": 1,
        "bimestral": 2,
        "trimestral": 3,
        "semestral": 6,
        "anual": 12,
    }
    return mapa.get(val, 1)


def _armar_buckets_proyeccion(
    usuario: Usuario,
    ref_hoy: date,
    horizonte: int,
) -> List[Dict[str, Any]]:
    ciclos: List[Dict[str, Any]] = []
    cursor = ref_hoy
    for _ in range(horizonte):
        ini, fin = get_ciclo_fechas(usuario, cursor)
        punto_medio = ini + (fin - ini) // 2
        mes_txt = MESES_CORTOS.get(punto_medio.month, str(punto_medio.month))
        anio_corto = str(punto_medio.year)[-2:]
        etiqueta = f"{mes_txt} {anio_corto}"
        rango_texto = f"{ini.strftime('%d/%m')} al {fin.strftime('%d/%m')}"
        clave = f"{punto_medio.year}-{punto_medio.month:02d}-{ini.isoformat()}"

        ciclos.append({
            "clave": clave,
            "etiqueta": etiqueta,
            "rango_texto": rango_texto,
            "fecha_inicio": ini,
            "fecha_fin": fin,
            "cuotas_dec": Decimal("0"),
            "suscripciones_dec": Decimal("0"),
            "cantidad_cuotas": 0,
            "planes_que_finalizan": 0,
            "liberacion_dec": Decimal("0"),
        })
        cursor = fin + timedelta(days=1)
    return ciclos


def _procesar_planes_cuotas(
    db: Session,
    usuario_id: UUID,
    ciclos: List[Dict[str, Any]],
    primer_ciclo_inicio: date,
    ultimo_ciclo_fin: date,
    modo: str,
    tc_oficial_hoy: Decimal,
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
) -> tuple[List[Dict[str, Any]], Decimal]:
    stmt_grupos = (
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas),
            selectinload(GrupoCuotas.tarjeta),
            selectinload(GrupoCuotas.transaccion_padre),
        )
        .where(
            GrupoCuotas.usuario_id == usuario_id,
            GrupoCuotas.estado == EstadoGrupoCuotas.ACTIVO,
        )
    )
    if tarjeta_id is not None:
        stmt_grupos = stmt_grupos.where(GrupoCuotas.tarjeta_id == tarjeta_id)

    grupos = db.execute(stmt_grupos).scalars().all()

    planes_activos: List[Dict[str, Any]] = []
    total_cuotas_pendientes_dec = Decimal("0")

    for g in grupos:
        if billetera_id is not None:
            b_padre = g.transaccion_padre.billetera_id if g.transaccion_padre else None
            b_tarj = g.tarjeta.billetera_id if g.tarjeta else None
            if b_padre != billetera_id and b_tarj != billetera_id:
                continue

        mon_g = (g.moneda.value if hasattr(g.moneda, "value") else str(g.moneda)).upper()
        cuotas_pend = [c for c in g.cuotas if not c.pagada]
        if not cuotas_pend:
            continue

        cuotas_pend.sort(key=lambda c: (c.fecha_vencimiento, c.numero_cuota))
        ultima_cuota = cuotas_pend[-1]

        if g.tarjeta:
            porcentaje_percep = Decimal(str(g.tarjeta.percepcion_moneda_extranjera))
            tc_cuota_usd_a_ars = tc_oficial_hoy * (Decimal("1") + (porcentaje_percep / Decimal("100")))
        else:
            tc_cuota_usd_a_ars = tc_oficial_hoy

        saldo_plan_dec = Decimal("0")
        monto_cuota_ref_dec = Decimal("0")

        for c in cuotas_pend:
            m_base = monto_efectivo_cuota(c)
            if modo == "solo_ars":
                if mon_g != "ARS":
                    continue
                m_conv = m_base
            elif modo == "solo_usd":
                if mon_g != "USD":
                    continue
                m_conv = m_base
            elif modo == "consolidado_ars":
                if mon_g == "ARS":
                    m_conv = m_base
                else:
                    m_conv = m_base * tc_cuota_usd_a_ars
            elif modo == "consolidado_usd":
                if mon_g == "USD":
                    m_conv = m_base
                else:
                    m_conv = (m_base / tc_oficial_hoy) if tc_oficial_hoy > 0 else Decimal("0")
            else:
                m_conv = m_base

            saldo_plan_dec += m_conv
            if monto_cuota_ref_dec == Decimal("0"):
                monto_cuota_ref_dec = m_conv

            venc = c.fecha_vencimiento
            if venc < primer_ciclo_inicio:
                ciclos[0]["cuotas_dec"] += m_conv
                ciclos[0]["cantidad_cuotas"] += 1
                total_cuotas_pendientes_dec += m_conv
            elif primer_ciclo_inicio <= venc <= ultimo_ciclo_fin:
                for b in ciclos:
                    if b["fecha_inicio"] <= venc <= b["fecha_fin"]:
                        b["cuotas_dec"] += m_conv
                        b["cantidad_cuotas"] += 1
                        total_cuotas_pendientes_dec += m_conv
                        break

        if saldo_plan_dec <= Decimal("0"):
            continue

        venc_ult = ultima_cuota.fecha_vencimiento
        if venc_ult < primer_ciclo_inicio:
            ciclos[0]["planes_que_finalizan"] += 1
            ciclos[0]["liberacion_dec"] += monto_cuota_ref_dec
        else:
            for b in ciclos:
                if b["fecha_inicio"] <= venc_ult <= b["fecha_fin"]:
                    b["planes_que_finalizan"] += 1
                    b["liberacion_dec"] += monto_cuota_ref_dec
                    break

        cuotas_pagadas_cnt = max(0, g.cantidad_cuotas - len(cuotas_pend))
        progreso_pct = (
            round((cuotas_pagadas_cnt / g.cantidad_cuotas) * 100.0, 1)
            if g.cantidad_cuotas > 0
            else 0.0
        )

        planes_activos.append({
            "id": str(g.id),
            "descripcion": g.descripcion,
            "tarjeta_nombre": g.tarjeta.nombre if g.tarjeta else "Tarjeta",
            "cuotas_restantes": len(cuotas_pend),
            "cantidad_cuotas": g.cantidad_cuotas,
            "monto_cuota": round(float(monto_cuota_ref_dec), 2),
            "saldo_pendiente": round(float(saldo_plan_dec), 2),
            "ultimo_vencimiento": ultima_cuota.fecha_vencimiento.isoformat(),
            "ultimo_mes_etiqueta": f"{MESES_CORTOS[ultima_cuota.fecha_vencimiento.month]} {str(ultima_cuota.fecha_vencimiento.year)[-2:]}",
            "progreso_pct": progreso_pct,
        })

    planes_activos.sort(key=lambda p: -p["saldo_pendiente"])
    return planes_activos, total_cuotas_pendientes_dec


def _procesar_suscripciones_futuras(
    db: Session,
    usuario_id: UUID,
    ciclos: List[Dict[str, Any]],
    primer_ciclo_inicio: date,
    ultimo_ciclo_fin: date,
    modo: str,
    tc_oficial_hoy: Decimal,
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
) -> Decimal:
    stmt_subs = (
        select(Suscripcion)
        .options(selectinload(Suscripcion.historial))
        .where(
            Suscripcion.usuario_id == usuario_id,
            Suscripcion.estado == EstadoSuscripcion.ACTIVA,
        )
    )
    if tarjeta_id is not None:
        stmt_subs = stmt_subs.where(Suscripcion.tarjeta_id == tarjeta_id)
    if billetera_id is not None:
        stmt_subs = stmt_subs.where(Suscripcion.billetera_id == billetera_id)

    suscripciones = db.execute(stmt_subs).scalars().all()
    carga_mensual_suscripciones_dec = Decimal("0")

    for sub in suscripciones:
        if not sub.historial:
            continue
        hist_ordenado = sorted(
            sub.historial,
            key=lambda h: (h.vigente_desde, h.fecha_creacion),
            reverse=True,
        )
        vigente = hist_ordenado[0]
        mon_s = (vigente.moneda.value if hasattr(vigente.moneda, "value") else str(vigente.moneda)).upper()
        m_base = Decimal(str(vigente.monto))

        if modo == "solo_ars":
            if mon_s != "ARS":
                continue
            m_sub_conv = m_base
        elif modo == "solo_usd":
            if mon_s != "USD":
                continue
            m_sub_conv = m_base
        elif modo == "consolidado_ars":
            if mon_s == "ARS":
                m_sub_conv = m_base
            else:
                m_sub_conv = m_base * tc_oficial_hoy
        elif modo == "consolidado_usd":
            if mon_s == "USD":
                m_sub_conv = m_base
            else:
                m_sub_conv = (m_base / tc_oficial_hoy) if tc_oficial_hoy > 0 else Decimal("0")
        else:
            m_sub_conv = m_base

        salto = _salto_meses_frecuencia(sub.frecuencia)
        carga_mensual_suscripciones_dec += m_sub_conv / Decimal(str(salto))

        d_cobro = sub.proximo_cobro
        guard = 0
        while d_cobro < primer_ciclo_inicio and guard < 120:
            y, m = _sumar_meses_calendario(d_cobro.year, d_cobro.month, salto)
            try:
                d_cobro = d_cobro.replace(year=y, month=m)
            except ValueError:
                d_cobro = date(y, m, 28)
            guard += 1

        while d_cobro <= ultimo_ciclo_fin and guard < 240:
            for b in ciclos:
                if b["fecha_inicio"] <= d_cobro <= b["fecha_fin"]:
                    b["suscripciones_dec"] += m_sub_conv
                    break
            y, m = _sumar_meses_calendario(d_cobro.year, d_cobro.month, salto)
            try:
                d_cobro = d_cobro.replace(year=y, month=m)
            except ValueError:
                d_cobro = date(y, m, 28)
            guard += 1

    return carga_mensual_suscripciones_dec


def obtener_proyeccion_compromisos(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    meses: int = 6,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    hoy: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Proyecta la carga futura de tarjetas, cuotas pendientes y suscripciones activas
    para los próximos ciclos financieros del usuario, identificando cuándo finalizan planes
    y cuánto flujo se libera.
    """
    ref_hoy = hoy or hoy_argentina()
    horizonte = max(3, min(12, meses))
    modo = (moneda_modo or "solo_ars").lower()
    if modo not in ("solo_ars", "solo_usd", "consolidado_ars", "consolidado_usd"):
        modo = "solo_ars"

    moneda_visual = "USD" if modo in ("solo_usd", "consolidado_usd") else "ARS"

    cot_hoy_map = dolar_service.obtener_cotizaciones_por_fechas(db, "oficial", [ref_hoy])
    cot_hoy = cot_hoy_map.get(ref_hoy)
    tc_oficial_hoy = Decimal(str(cot_hoy.venta)) if (cot_hoy and cot_hoy.venta and cot_hoy.venta > 0) else Decimal("0")

    ciclos = _armar_buckets_proyeccion(usuario, ref_hoy, horizonte)
    primer_ciclo_inicio = ciclos[0]["fecha_inicio"]
    ultimo_ciclo_fin = ciclos[-1]["fecha_fin"]

    planes_activos, total_cuotas_pendientes_dec = _procesar_planes_cuotas(
        db, usuario.id, ciclos, primer_ciclo_inicio, ultimo_ciclo_fin,
        modo, tc_oficial_hoy, billetera_id, tarjeta_id
    )

    carga_mensual_suscripciones_dec = _procesar_suscripciones_futuras(
        db, usuario.id, ciclos, primer_ciclo_inicio, ultimo_ciclo_fin,
        modo, tc_oficial_hoy, billetera_id, tarjeta_id
    )

    serie_mensual: List[Dict[str, Any]] = []
    mes_mayor_alivio: Optional[Dict[str, Any]] = None
    max_liberacion = Decimal("0")

    for b in ciclos:
        c_dec = b["cuotas_dec"]
        s_dec = b["suscripciones_dec"]
        tot_dec = c_dec + s_dec
        lib_dec = b["liberacion_dec"]

        if lib_dec > max_liberacion:
            max_liberacion = lib_dec
            mes_mayor_alivio = {
                "etiqueta": b["etiqueta"],
                "rango_texto": b["rango_texto"],
                "monto_liberado": round(float(lib_dec), 2),
                "planes_finalizados": b["planes_que_finalizan"],
            }

        serie_mensual.append({
            "clave": b["clave"],
            "etiqueta": b["etiqueta"],
            "rango_texto": b["rango_texto"],
            "monto_cuotas": round(float(c_dec), 2),
            "monto_suscripciones": round(float(s_dec), 2),
            "monto_total": round(float(tot_dec), 2),
            "cantidad_cuotas": b["cantidad_cuotas"],
            "planes_que_finalizan": b["planes_que_finalizan"],
            "monto_que_se_libera": round(float(lib_dec), 2),
        })

    compromiso_mes_actual = serie_mensual[0]["monto_total"] if serie_mensual else 0.0

    return {
        "moneda_visual": moneda_visual,
        "horizonte_meses": horizonte,
        "cotizacion_referencia": {
            "texto": "Las cuotas en dólares están pasadas con el dólar oficial de hoy más el impuesto de la tarjeta.",
        },
        "kpis": {
            "compromiso_mes_actual": compromiso_mes_actual,
            "total_cuotas_ventana": round(float(total_cuotas_pendientes_dec), 2),
            "carga_mensual_suscripciones": round(float(carga_mensual_suscripciones_dec), 2),
            "cantidad_planes_activos": len(planes_activos),
            "mes_mayor_alivio": mes_mayor_alivio,
        },
        "serie_mensual": serie_mensual,
        "planes_activos": planes_activos[:15],
    }
