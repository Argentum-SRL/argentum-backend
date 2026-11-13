from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.grupo_cuotas import GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import OrigenTransaccion, Transaccion
from app.models.usuario import Moneda, Usuario
from app.services import dolar_service
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto

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


def _obtener_mapa_cotizaciones(
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


def _convertir_movimiento(
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


def _aplicar_filtros_extra(
    cond_base: Any,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    categoria_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
) -> Any:
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


def _sumar_periodo(
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
    cond_g = _aplicar_filtros_extra(
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
    cots_g = _obtener_mapa_cotizaciones(db, modo, fechas_g)
    gasto_tot = Decimal("0")
    for f_tx, mon_tx, monto_tx in rows_g:
        m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
        m_conv, _ = _convertir_movimiento(Decimal(str(monto_tx)), m_code, f_tx, modo, cots_g)
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
        cots_i = _obtener_mapa_cotizaciones(db, modo, fechas_i)
        for f_tx, mon_tx, monto_tx in rows_i:
            m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
            m_conv, _ = _convertir_movimiento(Decimal(str(monto_tx)), m_code, f_tx, modo, cots_i)
            if m_conv is not None:
                ingreso_tot += m_conv

    return gasto_tot, ingreso_tot


def obtener_resumen_analisis(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    cantidad_ciclos: int = 6,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    categoria_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
    hoy: Optional[date] = None,
) -> Dict[str, Any]:
    ref_hoy = hoy or hoy_argentina()
    modo = (moneda_modo or "solo_ars").lower()
    if modo not in ("solo_ars", "solo_usd", "consolidado_ars", "consolidado_usd"):
        modo = "solo_ars"

    moneda_visual = "USD" if modo in ("solo_usd", "consolidado_usd") else "ARS"
    solo_gastos = bool(categoria_id or tarjeta_id)

    # Obtenemos los ciclos pedidos más ciclos anteriores para calcular variaciones
    ciclos_con_prev = generar_ventana_ciclos(usuario, ref_hoy, max(2, cantidad_ciclos + 1))
    cant_n = max(1, min(24, cantidad_ciclos))
    ciclos = ciclos_con_prev[-cant_n:]
    fecha_inicio_global = ciclos[0]["fecha_inicio"]
    fecha_fin_global = min(ciclos[-1]["fecha_fin"], ref_hoy)

    # ── 1. Consultas para la ventana seleccionada ────────────────────────────
    cond_ventana_gasto = _aplicar_filtros_extra(
        condicion_gasto(usuario.id, desde=fecha_inicio_global, hasta=fecha_fin_global, hoy=ref_hoy),
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=categoria_id,
        naturaleza=naturaleza,
    )
    rows_g_diarios = db.execute(
        select(
            Transaccion.fecha,
            Transaccion.moneda,
            Transaccion.categoria_id,
            Transaccion.subcategoria_id,
            func.coalesce(func.sum(Transaccion.monto), 0),
            func.count(Transaccion.id),
        )
        .where(cond_ventana_gasto)
        .group_by(
            Transaccion.fecha,
            Transaccion.moneda,
            Transaccion.categoria_id,
            Transaccion.subcategoria_id,
        )
    ).all()

    rows_i_diarios = []
    if not solo_gastos:
        cond_i_ventana_base = condicion_ingreso(
            usuario.id, desde=fecha_inicio_global, hasta=fecha_fin_global, hoy=ref_hoy
        )
        cond_ventana_ingreso = (
            and_(cond_i_ventana_base, Transaccion.billetera_id == billetera_id)
            if billetera_id
            else cond_i_ventana_base
        )
        rows_i_diarios = db.execute(
            select(
                Transaccion.fecha,
                Transaccion.moneda,
                func.coalesce(func.sum(Transaccion.monto), 0),
                func.count(Transaccion.id),
            )
            .where(cond_ventana_ingreso)
            .group_by(Transaccion.fecha, Transaccion.moneda)
        ).all()

    # Recolectar fechas para cotizaciones si el modo es consolidado
    fechas_a_convertir: set[date] = set()
    if modo == "consolidado_ars":
        for r in rows_g_diarios:
            m_code = r[1].value if hasattr(r[1], "value") else str(r[1])
            if m_code.upper() == "USD":
                fechas_a_convertir.add(r[0])
        for r in rows_i_diarios:
            m_code = r[1].value if hasattr(r[1], "value") else str(r[1])
            if m_code.upper() == "USD":
                fechas_a_convertir.add(r[0])
    elif modo == "consolidado_usd":
        for r in rows_g_diarios:
            m_code = r[1].value if hasattr(r[1], "value") else str(r[1])
            if m_code.upper() == "ARS":
                fechas_a_convertir.add(r[0])
        for r in rows_i_diarios:
            m_code = r[1].value if hasattr(r[1], "value") else str(r[1])
            if m_code.upper() == "ARS":
                fechas_a_convertir.add(r[0])

    cotizaciones_map = _obtener_mapa_cotizaciones(db, modo, fechas_a_convertir)

    # ── 2. Procesamiento de Serie por Ciclo ───────────────────────────────────
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
                    m_conv, sin_cot = _convertir_movimiento(m_dec, m_code, f_tx, modo, cotizaciones_map)
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
                        m_conv, sin_cot = _convertir_movimiento(m_dec, m_code, f_tx, modo, cotizaciones_map)
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

    # ── 3. Promedios y Tasa de Ahorro con ciclos completos ────────────────────
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

    # ── 4. Variación vs. Ciclo Anterior ──────────────────────────────────────
    variacion_gasto_vs_ant: Optional[float] = None
    variacion_ingreso_vs_ant: Optional[float] = None
    variacion_texto: Optional[str] = None

    ciclo_ult_meta = ciclos[-1]
    es_ult_en_curso = (ciclo_ult_meta["fecha_fin"] >= ref_hoy)
    ciclo_ant_meta = ciclos_con_prev[-2] if len(ciclos_con_prev) >= 2 else None

    if ciclo_ant_meta is not None:
        if es_ult_en_curso:
            dias_n = (ref_hoy - ciclo_ult_meta["fecha_inicio"]).days + 1
            gasto_actual_n = serie_ciclos_raw[-1]["_gasto_dec"]
            ingreso_actual_n = serie_ciclos_raw[-1]["_ingreso_dec"]

            ant_ini = ciclo_ant_meta["fecha_inicio"]
            ant_fin_n = min(ant_ini + timedelta(days=dias_n - 1), ciclo_ant_meta["fecha_fin"])

            gasto_ant_n, ingreso_ant_n = _sumar_periodo(
                db, usuario.id, ant_ini, ant_fin_n, ref_hoy, modo,
                billetera_id, tarjeta_id, categoria_id, naturaleza, solo_gastos
            )

            if gasto_ant_n > 0:
                variacion_gasto_vs_ant = round(
                    float(((gasto_actual_n - gasto_ant_n) / gasto_ant_n) * Decimal("100")),
                    1,
                )
                variacion_texto = f"Comparado con los mismos {dias_n} días del ciclo anterior"

            if not solo_gastos and ingreso_actual_n is not None and ingreso_ant_n and ingreso_ant_n > 0:
                variacion_ingreso_vs_ant = round(
                    float(((ingreso_actual_n - ingreso_ant_n) / ingreso_ant_n) * Decimal("100")),
                    1,
                )
        else:
            gasto_actual = serie_ciclos_raw[-1]["_gasto_dec"]
            ingreso_actual = serie_ciclos_raw[-1]["_ingreso_dec"]
            ant_ini = ciclo_ant_meta["fecha_inicio"]
            ant_fin = ciclo_ant_meta["fecha_fin"]

            gasto_ant, ingreso_ant = _sumar_periodo(
                db, usuario.id, ant_ini, ant_fin, ref_hoy, modo,
                billetera_id, tarjeta_id, categoria_id, naturaleza, solo_gastos
            )

            if gasto_ant > 0:
                variacion_gasto_vs_ant = round(
                    float(((gasto_actual - gasto_ant) / gasto_ant) * Decimal("100")),
                    1,
                )
                variacion_texto = "Comparado con el ciclo anterior completo"

            if not solo_gastos and ingreso_actual is not None and ingreso_ant and ingreso_ant > 0:
                variacion_ingreso_vs_ant = round(
                    float(((ingreso_actual - ingreso_ant) / ingreso_ant) * Decimal("100")),
                    1,
                )

    # ── 5. Desglose por Categoría y Subcategoría (Suma neta) ─────────────────
    stmt_desglose = (
        select(
            Transaccion.fecha,
            Transaccion.moneda,
            Transaccion.categoria_id,
            Categoria.nombre.label("categoria_nombre"),
            Transaccion.subcategoria_id,
            Subcategoria.nombre.label("subcategoria_nombre"),
            func.coalesce(func.sum(Transaccion.monto), 0).label("total_monto"),
            func.count(Transaccion.id).label("cant_tx"),
        )
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
        .where(cond_ventana_gasto)
        .group_by(
            Transaccion.fecha,
            Transaccion.moneda,
            Transaccion.categoria_id,
            Categoria.nombre,
            Transaccion.subcategoria_id,
            Subcategoria.nombre,
        )
    )
    rows_desglose = db.execute(stmt_desglose).all()

    fechas_desglose = {r[0] for r in rows_desglose}
    cots_desglose = _obtener_mapa_cotizaciones(db, modo, fechas_desglose)

    cat_acum: Dict[str, Dict[str, Any]] = {}
    for r in rows_desglose:
        f_tx, mon_tx, cat_id, cnombre, sub_id, snombre, monto_tx, cant_tx = r
        m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
        m_conv, _ = _convertir_movimiento(Decimal(str(monto_tx)), m_code, f_tx, modo, cots_desglose)
        if m_conv is None:
            continue

        cid_str = str(cat_id) if cat_id else "sin-categoria"
        c_name = cnombre or "Otros"
        sid_str = str(sub_id) if sub_id else "general"
        s_name = snombre or "Otros"
        cant = int(cant_tx or 0)

        if cid_str not in cat_acum:
            cat_acum[cid_str] = {
                "categoria_id": str(cat_id) if cat_id else None,
                "categoria_nombre": c_name,
                "monto": Decimal("0"),
                "cantidad_movimientos": 0,
                "subs": {},
            }

        cat_entry = cat_acum[cid_str]
        cat_entry["monto"] += m_conv
        cat_entry["cantidad_movimientos"] += cant

        subs_map = cat_entry["subs"]
        if sid_str not in subs_map:
            subs_map[sid_str] = {
                "subcategoria_id": sid_str,
                "subcategoria_nombre": s_name,
                "monto": Decimal("0"),
                "cantidad_movimientos": 0,
            }
        subs_map[sid_str]["monto"] += m_conv
        subs_map[sid_str]["cantidad_movimientos"] += cant

    # Filtrar categorías con suma neta positiva
    cats_validas = [c for c in cat_acum.values() if c["monto"] > Decimal("0")]
    cats_ordenadas = sorted(cats_validas, key=lambda x: -x["monto"])
    suma_cats_listadas = sum((c["monto"] for c in cats_ordenadas), Decimal("0"))
    max_cat_monto = cats_ordenadas[0]["monto"] if cats_ordenadas else Decimal("0")

    desglose_categorias: List[Dict[str, Any]] = []
    for c_item in cats_ordenadas:
        c_monto: Decimal = c_item["monto"]
        pct_total = (
            round(float((c_monto / suma_cats_listadas) * Decimal("100")), 1)
            if suma_cats_listadas > 0
            else 0.0
        )
        ancho_barra = (
            round(float((c_monto / max_cat_monto) * Decimal("100")), 1)
            if max_cat_monto > 0
            else 0.0
        )

        subs_validas = [s for s in c_item["subs"].values() if s["monto"] > Decimal("0")]
        subs_ordenadas = sorted(subs_validas, key=lambda s: -s["monto"])
        max_sub_monto = subs_ordenadas[0]["monto"] if subs_ordenadas else Decimal("0")

        subcategorias_out: List[Dict[str, Any]] = []
        for s_item in subs_ordenadas:
            s_monto: Decimal = s_item["monto"]
            pct_cat = (
                round(float((s_monto / c_monto) * Decimal("100")), 1)
                if c_monto > 0
                else 0.0
            )
            pct_global = (
                round(float((s_monto / suma_cats_listadas) * Decimal("100")), 1)
                if suma_cats_listadas > 0
                else 0.0
            )
            ancho_sub = (
                round(float((s_monto / max_sub_monto) * Decimal("100")), 1)
                if max_sub_monto > 0
                else 0.0
            )
            subcategorias_out.append({
                "subcategoria_id": s_item["subcategoria_id"],
                "subcategoria_nombre": s_item["subcategoria_nombre"],
                "monto": round(float(s_monto), 2),
                "porcentaje_categoria": pct_cat,
                "porcentaje_total": pct_global,
                "ancho_barra_pct": ancho_sub,
                "cantidad_movimientos": s_item["cantidad_movimientos"],
            })

        desglose_categorias.append({
            "categoria_id": c_item["categoria_id"],
            "categoria_nombre": c_item["categoria_nombre"],
            "monto": round(float(c_monto), 2),
            "porcentaje_del_total": pct_total,
            "ancho_barra_pct": ancho_barra,
            "cantidad_movimientos": c_item["cantidad_movimientos"],
            "subcategorias": subcategorias_out,
        })

    # ── 6. Totales de Ventana y Balance ──────────────────────────────────────
    suma_gastos_ventana = sum((item["gastos"] for item in serie_ciclos), 0.0)
    if solo_gastos:
        suma_ingresos_ventana = None
        balance_ventana = None
    else:
        suma_ingresos_ventana = sum(
            (item["ingresos"] for item in serie_ciclos if item["ingresos"] is not None),
            0.0,
        )
        balance_ventana = round(suma_ingresos_ventana - suma_gastos_ventana, 2)

    # ── 7. Insights Deterministas ────────────────────────────────────────────
    insights: List[Dict[str, Any]] = []

    if desglose_categorias:
        top_cat = desglose_categorias[0]
        insights.append({
            "id": "top-categoria",
            "tipo": "info",
            "titulo": f"{top_cat['categoria_nombre']} lidera tus gastos ({top_cat['porcentaje_del_total']}%)",
            "descripcion": f"Son {top_cat['cantidad_movimientos']} movimientos en estos ciclos.",
        })

    if variacion_gasto_vs_ant is not None:
        if variacion_gasto_vs_ant <= -5:
            if es_ult_en_curso:
                titulo_var = f"En lo que va del ciclo gastaste {abs(variacion_gasto_vs_ant):g}% menos que en los mismos días del anterior"
            else:
                titulo_var = f"Gastaste {abs(variacion_gasto_vs_ant):g}% menos que el ciclo anterior"
            insights.append({
                "id": "variacion-gasto",
                "tipo": "positivo",
                "titulo": titulo_var,
                "descripcion": "Buen ritmo respecto al ciclo anterior.",
            })
        elif variacion_gasto_vs_ant >= 10:
            if es_ult_en_curso:
                titulo_var = f"En lo que va del ciclo gastaste {variacion_gasto_vs_ant:g}% más que en los mismos días del anterior"
            else:
                titulo_var = f"Gastaste {variacion_gasto_vs_ant:g}% más que el ciclo anterior"
            insights.append({
                "id": "variacion-gasto",
                "tipo": "alerta",
                "titulo": titulo_var,
                "descripcion": "Revisá el desglose por categorías para ver en qué rubros aumentó el gasto.",
            })

    if ciclos_completos and not solo_gastos and tasa_ahorro_ventana is not None:
        if tasa_ahorro_ventana >= 20:
            insights.append({
                "id": "tasa-ahorro",
                "tipo": "positivo",
                "titulo": f"Te quedó el {tasa_ahorro_ventana:g}% de lo que entró",
                "descripcion": "Estás reteniendo más de una quinta parte de lo que entró.",
            })
        elif tasa_ahorro_ventana < 0:
            sum_g_c = sum(item["_gasto_dec"] for item in ciclos_completos)
            sum_i_c = sum(
                (item["_ingreso_dec"] for item in ciclos_completos if item["_ingreso_dec"] is not None),
                Decimal("0"),
            )
            dif_monto = abs(sum_i_c - sum_g_c)
            moneda_fmt = "ARS" if modo.endswith("ars") or modo == "solo_ars" else "USD"
            monto_str = formatear_monto(dif_monto, moneda_fmt)
            insights.append({
                "id": "tasa-ahorro",
                "tipo": "alerta",
                "titulo": "Gastaste más de lo que entró",
                "descripcion": f"En estos ciclos salieron {monto_str} más de lo que entró.",
            })

    # ── 8. Filtros disponibles ───────────────────────────────────────────────
    billeteras_activas = (
        db.execute(
            select(Billetera)
            .where(
                Billetera.usuario_id == usuario.id,
                Billetera.estado == EstadoBilletera.ACTIVA,
                Billetera.es_inversion.is_(False),
            )
            .order_by(Billetera.es_principal.desc(), Billetera.nombre.asc())
        )
        .scalars()
        .all()
    )
    tarjetas_usuario = (
        db.execute(
            select(TarjetaCredito)
            .where(TarjetaCredito.usuario_id == usuario.id)
            .order_by(TarjetaCredito.nombre.asc())
        )
        .scalars()
        .all()
    )

    return {
        "moneda_modo": modo,
        "moneda_visual": moneda_visual,
        "movimientos_sin_cotizacion": movimientos_sin_cotizacion,
        "cotizacion_referencia": {
            "texto": "Pasamos cada movimiento con el dólar oficial del día en que se hizo.",
        },
        "periodo_seleccionado": {
            "cantidad_ciclos": len(ciclos),
            "fecha_inicio": fecha_inicio_global.isoformat(),
            "fecha_fin": fecha_fin_global.isoformat(),
            "etiqueta": (
                f"Ciclo actual ({ciclos[-1]['rango_texto']})"
                if len(ciclos) == 1
                else f"Últimos {len(ciclos)} ciclos ({ciclos[0]['etiqueta']} — {ciclos[-1]['etiqueta']})"
            ),
        },
        "kpis": {
            "ingresos_totales": round(suma_ingresos_ventana, 2) if suma_ingresos_ventana is not None else None,
            "gastos_totales": round(suma_gastos_ventana, 2),
            "balance_neto": balance_ventana,
            "tasa_ahorro_pct": tasa_ahorro_ventana,
            "promedio_gasto_ciclo": promedio_gasto_ciclo,
            "promedio_ingreso_ciclo": promedio_ingreso_ciclo,
            "variacion_gasto_vs_anterior_pct": variacion_gasto_vs_ant,
            "variacion_ingreso_vs_anterior_pct": variacion_ingreso_vs_ant,
            "variacion_texto": variacion_texto,
            "cantidad_operaciones": total_operaciones_gasto,
        },
        "insights": insights,
        "serie_ciclos": serie_ciclos,
        "desglose_categorias": desglose_categorias,
        "filtros_disponibles": {
            "billeteras": [
                {
                    "id": str(b.id),
                    "nombre": b.nombre,
                    "moneda": b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda),
                }
                for b in billeteras_activas
            ],
            "tarjetas": [
                {
                    "id": str(t.id),
                    "nombre": t.nombre,
                    "red": t.red.value if hasattr(t.red, "value") else str(t.red),
                    "ultimos_4": t.apodo or (t.red.value.upper() if hasattr(t.red, "value") else str(t.red).upper()),
                }
                for t in tarjetas_usuario
            ],
        },
    }


def obtener_movimientos_drilldown(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    cantidad_ciclos: int = 6,
    categoria_id: Optional[str] = None,
    subcategoria_id: Optional[str] = None,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
    limit: int = 40,
    hoy: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """
    Devuelve las transacciones reales que componen una categoría/subcategoría
    dentro de la ventana y filtros seleccionados en Análisis.
    """
    ref_hoy = hoy or hoy_argentina()
    modo = (moneda_modo or "solo_ars").lower()
    ciclos = generar_ventana_ciclos(usuario, ref_hoy, max(1, min(24, cantidad_ciclos)))
    fecha_inicio_global = ciclos[0]["fecha_inicio"]
    fecha_fin_global = min(ciclos[-1]["fecha_fin"], ref_hoy)

    cat_uuid: Optional[UUID] = None
    filtrar_sin_categoria = False
    if categoria_id:
        if categoria_id == "sin-categoria":
            filtrar_sin_categoria = True
        else:
            try:
                cat_uuid = UUID(categoria_id)
            except ValueError:
                pass

    cond_base = condicion_gasto(usuario.id, desde=fecha_inicio_global, hasta=fecha_fin_global, hoy=ref_hoy)
    cond = _aplicar_filtros_extra(
        cond_base,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=cat_uuid,
        naturaleza=naturaleza,
    )

    if filtrar_sin_categoria:
        cond = and_(cond, Transaccion.categoria_id.is_(None))

    if subcategoria_id:
        if subcategoria_id == "general":
            cond = and_(cond, Transaccion.subcategoria_id.is_(None))
        else:
            try:
                sub_uuid = UUID(subcategoria_id)
                cond = and_(cond, Transaccion.subcategoria_id == sub_uuid)
            except ValueError:
                pass

    if modo == "solo_ars":
        cond = and_(cond, Transaccion.moneda == Moneda.ARS)
    elif modo == "solo_usd":
        cond = and_(cond, Transaccion.moneda == Moneda.USD)

    stmt = (
        select(Transaccion)
        .options(
            selectinload(Transaccion.categoria),
            selectinload(Transaccion.subcategoria),
            selectinload(Transaccion.billetera),
            selectinload(Transaccion.tarjeta),
        )
        .where(cond)
        .order_by(Transaccion.fecha.desc(), Transaccion.fecha_creacion.desc())
        .limit(max(1, min(100, limit)))
    )
    txs = db.execute(stmt).scalars().all()

    grupo_ids = {t.grupo_cuotas_id for t in txs if t.grupo_cuotas_id}
    grupos_map: Dict[UUID, int] = {}
    if grupo_ids:
        rows_g = db.execute(
            select(GrupoCuotas.id, GrupoCuotas.cantidad_cuotas).where(GrupoCuotas.id.in_(grupo_ids))
        ).all()
        grupos_map = {r[0]: int(r[1]) for r in rows_g}

    items: List[Dict[str, Any]] = []
    for tx in txs:
        mon_str = tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda)
        tipo_str = tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo)
        cant_cuotas = grupos_map.get(tx.grupo_cuotas_id) if tx.grupo_cuotas_id else None
        cuota_txt = f"{cant_cuotas} cuotas" if cant_cuotas and cant_cuotas > 1 else None

        items.append({
            "id": str(tx.id),
            "descripcion": tx.descripcion or (tx.subcategoria.nombre if tx.subcategoria else "Movimiento"),
            "fecha": tx.fecha.isoformat(),
            "monto": float(tx.monto),
            "moneda": mon_str,
            "tipo": tipo_str,
            "categoria_nombre": tx.categoria.nombre if tx.categoria else "Otros",
            "subcategoria_nombre": tx.subcategoria.nombre if tx.subcategoria else None,
            "billetera_nombre": tx.billetera.nombre if tx.billetera else "Billetera",
            "tarjeta_nombre": tx.tarjeta.nombre if tx.tarjeta else None,
            "cuota_texto": cuota_txt,
            "es_suscripcion": bool(tx.suscripcion_id is not None),
        })

    return items


def obtener_comparativa_ciclos(
    db: Session,
    usuario: Usuario,
    moneda_modo: str = "solo_ars",
    ciclo_a_clave: Optional[str] = None,
    ciclo_b_clave: Optional[str] = None,
    billetera_id: Optional[UUID] = None,
    tarjeta_id: Optional[UUID] = None,
    naturaleza: Optional[str] = None,
    hoy: Optional[date] = None,
) -> Dict[str, Any]:
    """
    Compara dos ciclos financieros devolviendo totales, diferencias y
    qué cambió entre ambos rubro por rubro.
    """
    ref_hoy = hoy or hoy_argentina()
    modo = (moneda_modo or "solo_ars").lower()
    if modo not in ("solo_ars", "solo_usd", "consolidado_ars", "consolidado_usd"):
        modo = "solo_ars"

    moneda_visual = "USD" if modo in ("solo_usd", "consolidado_usd") else "ARS"
    solo_gastos = bool(tarjeta_id)

    catalogo_ciclos = generar_ventana_ciclos(usuario, ref_hoy, 12)
    mapa_ciclos = {c["clave"]: c for c in catalogo_ciclos}

    ciclo_a = mapa_ciclos.get(ciclo_a_clave or "") or catalogo_ciclos[-1]
    ciclo_b_default = catalogo_ciclos[-2] if len(catalogo_ciclos) >= 2 else catalogo_ciclos[-1]
    ciclo_b = mapa_ciclos.get(ciclo_b_clave or "") or ciclo_b_default

    def _agrupar_ciclo(ciclo_meta: Dict[str, Any]) -> tuple[Decimal, Decimal, Dict[str, Dict[str, Any]]]:
        c_ini: date = ciclo_meta["fecha_inicio"]
        c_fin: date = min(ciclo_meta["fecha_fin"], ref_hoy)
        if c_ini > ref_hoy:
            return Decimal("0"), Decimal("0"), {}

        cond_g = _aplicar_filtros_extra(
            condicion_gasto(usuario.id, desde=c_ini, hasta=c_fin, hoy=ref_hoy),
            billetera_id=billetera_id,
            tarjeta_id=tarjeta_id,
            naturaleza=naturaleza,
        )
        rows_cat = db.execute(
            select(
                Transaccion.fecha,
                Transaccion.moneda,
                Transaccion.categoria_id,
                Categoria.nombre.label("categoria_nombre"),
                func.coalesce(func.sum(Transaccion.monto), 0).label("total_monto"),
            )
            .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
            .where(cond_g)
            .group_by(Transaccion.fecha, Transaccion.moneda, Transaccion.categoria_id, Categoria.nombre)
        ).all()

        fechas_g = {r[0] for r in rows_cat}
        cots_g = _obtener_mapa_cotizaciones(db, modo, fechas_g)

        por_cat: Dict[str, Dict[str, Any]] = {}
        gasto_total = Decimal("0")
        for f_tx, mon_tx, cat_id, cnombre, total_m in rows_cat:
            m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
            m_conv, _ = _convertir_movimiento(Decimal(str(total_m or 0)), m_code, f_tx, modo, cots_g)
            if m_conv is None:
                continue

            gasto_total += m_conv
            cid = str(cat_id) if cat_id else "sin-categoria"
            c_name = cnombre or "Otros"
            if cid not in por_cat:
                por_cat[cid] = {
                    "categoria_id": str(cat_id) if cat_id else None,
                    "categoria_nombre": c_name,
                    "monto": Decimal("0"),
                }
            por_cat[cid]["monto"] += m_conv

        ingreso_total = Decimal("0")
        if not solo_gastos:
            cond_i_base = condicion_ingreso(usuario.id, desde=c_ini, hasta=c_fin, hoy=ref_hoy)
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
            cots_i = _obtener_mapa_cotizaciones(db, modo, fechas_i)
            for f_tx, mon_tx, total_m in rows_i:
                m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
                m_conv, _ = _convertir_movimiento(Decimal(str(total_m or 0)), m_code, f_tx, modo, cots_i)
                if m_conv is not None:
                    ingreso_total += m_conv

        return gasto_total, ingreso_total, por_cat

    gasto_a, ingreso_a, cats_a = _agrupar_ciclo(ciclo_a)
    gasto_b, ingreso_b, cats_b = _agrupar_ciclo(ciclo_b)

    todas_claves = set(cats_a.keys()) | set(cats_b.keys())
    items_raw: List[Dict[str, Any]] = []
    max_monto_cat = Decimal("0")

    for cid in todas_claves:
        info_a = cats_a.get(cid)
        info_b = cats_b.get(cid)
        info_base = info_a or info_b or {}
        nombre = info_base.get("categoria_nombre", "Otros")
        cat_id_real = info_base.get("categoria_id", cid)
        ma = info_a["monto"] if info_a else Decimal("0")
        mb = info_b["monto"] if info_b else Decimal("0")

        # Excluir si en ambos ciclos es 0 o negativo
        if ma <= Decimal("0") and mb <= Decimal("0"):
            continue

        mayor = max(ma, mb)
        if mayor > max_monto_cat:
            max_monto_cat = mayor

        diff = ma - mb
        diff_pct: Optional[float] = None
        if mb > 0:
            diff_pct = round(float((diff / mb) * Decimal("100")), 1)

        if diff > Decimal("0.01"):
            tendencia = "subio"
        elif diff < Decimal("-0.01"):
            tendencia = "bajo"
        else:
            tendencia = "igual"

        items_raw.append({
            "categoria_id": cat_id_real,
            "categoria_nombre": nombre,
            "monto_a_dec": ma,
            "monto_b_dec": mb,
            "diff_dec": diff,
            "monto_a": round(float(ma), 2),
            "monto_b": round(float(mb), 2),
            "diferencia_monto": round(float(diff), 2),
            "diferencia_pct": diff_pct,
            "tendencia": tendencia,
        })

    items_raw.sort(key=lambda x: -max(x["monto_a_dec"], x["monto_b_dec"]))

    mayor_aumento: Optional[Dict[str, Any]] = None
    mayor_ahorro: Optional[Dict[str, Any]] = None

    aumentos = [x for x in items_raw if x["diff_dec"] > 0]
    if aumentos:
        top_up = max(aumentos, key=lambda x: x["diff_dec"])
        mayor_aumento = {
            "categoria_nombre": top_up["categoria_nombre"],
            "diferencia_monto": top_up["diferencia_monto"],
            "diferencia_pct": top_up["diferencia_pct"],
        }

    ahorros = [x for x in items_raw if x["diff_dec"] < 0]
    if ahorros:
        top_down = min(ahorros, key=lambda x: x["diff_dec"])
        mayor_ahorro = {
            "categoria_nombre": top_down["categoria_nombre"],
            "diferencia_monto": abs(top_down["diferencia_monto"]),
            "diferencia_pct": abs(top_down["diferencia_pct"]) if top_down["diferencia_pct"] is not None else None,
        }

    categorias_out: List[Dict[str, Any]] = []
    for it in items_raw:
        ancho_a = (
            round(float((it["monto_a_dec"] / max_monto_cat) * Decimal("100")), 1)
            if max_monto_cat > 0
            else 0.0
        )
        ancho_b = (
            round(float((it["monto_b_dec"] / max_monto_cat) * Decimal("100")), 1)
            if max_monto_cat > 0
            else 0.0
        )
        categorias_out.append({
            "categoria_id": it["categoria_id"],
            "categoria_nombre": it["categoria_nombre"],
            "monto_a": it["monto_a"],
            "monto_b": it["monto_b"],
            "diferencia_monto": it["diferencia_monto"],
            "diferencia_pct": it["diferencia_pct"],
            "tendencia": it["tendencia"],
            "ancho_a_pct": ancho_a,
            "ancho_b_pct": ancho_b,
        })

    diff_gasto_total = gasto_a - gasto_b
    diff_gasto_pct = (
        round(float((diff_gasto_total / gasto_b) * Decimal("100")), 1)
        if gasto_b > 0
        else None
    )
    diff_ingreso_total = ingreso_a - ingreso_b
    diff_ingreso_pct = (
        round(float((diff_ingreso_total / ingreso_b) * Decimal("100")), 1)
        if ingreso_b > 0 and not solo_gastos
        else None
    )

    return {
        "moneda_visual": moneda_visual,
        "ciclos_disponibles": [
            {
                "clave": c["clave"],
                "etiqueta": c["etiqueta"],
                "rango_texto": c["rango_texto"],
                "es_actual": c["es_actual"],
                "dias_transcurridos": c["dias_transcurridos"],
                "dias_totales": c["dias_totales"],
            }
            for c in reversed(catalogo_ciclos)
        ],
        "ciclo_a": {
            "clave": ciclo_a["clave"],
            "etiqueta": ciclo_a["etiqueta"],
            "rango_texto": ciclo_a["rango_texto"],
            "dias_transcurridos": ciclo_a["dias_transcurridos"],
            "dias_totales": ciclo_a["dias_totales"],
        },
        "ciclo_b": {
            "clave": ciclo_b["clave"],
            "etiqueta": ciclo_b["etiqueta"],
            "rango_texto": ciclo_b["rango_texto"],
            "dias_transcurridos": ciclo_b["dias_transcurridos"],
            "dias_totales": ciclo_b["dias_totales"],
        },
        "resumen": {
            "gasto_a": round(float(gasto_a), 2),
            "gasto_b": round(float(gasto_b), 2),
            "diferencia_gasto_monto": round(float(diff_gasto_total), 2),
            "diferencia_gasto_pct": diff_gasto_pct,
            "ingreso_a": round(float(ingreso_a), 2) if not solo_gastos else None,
            "ingreso_b": round(float(ingreso_b), 2) if not solo_gastos else None,
            "diferencia_ingreso_monto": round(float(diff_ingreso_total), 2) if not solo_gastos else None,
            "diferencia_ingreso_pct": diff_ingreso_pct,
            "balance_a": round(float(ingreso_a - gasto_a), 2) if not solo_gastos else None,
            "balance_b": round(float(ingreso_b - gasto_b), 2) if not solo_gastos else None,
            "mayor_aumento": mayor_aumento,
            "mayor_ahorro": mayor_ahorro,
        },
        "categorias": categorias_out,
    }


# ── Re-exportación de Proyección, Informes y Excel (.xlsx) ────────────────────
from app.services.analisis_informes_service import (
    actualizar_informe_usuario,
    crear_informe_usuario,
    eliminar_informe_usuario,
    generar_excel_analisis,
    listar_informes_usuario,
    obtener_informe_usuario,
    obtener_proyeccion_compromisos,
)

__all__ = [
    "generar_ventana_ciclos",
    "obtener_resumen_analisis",
    "obtener_movimientos_drilldown",
    "obtener_comparativa_ciclos",
    "obtener_proyeccion_compromisos",
    "listar_informes_usuario",
    "obtener_informe_usuario",
    "crear_informe_usuario",
    "actualizar_informe_usuario",
    "eliminar_informe_usuario",
    "generar_excel_analisis",
]
