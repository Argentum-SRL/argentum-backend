from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import OrigenTransaccion, Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.dashboard_service import get_ciclo_fechas, get_cotizacion_usuario
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.utils.cuotas import monto_efectivo_cuota
from app.utils.fecha import hoy_argentina

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


def generar_ventana_ciclos(
    usuario: Usuario,
    hoy: date,
    cantidad_ciclos: int,
    db: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """
    Genera la lista cronológica de los últimos `cantidad_ciclos` ciclos financieros del usuario,
    desde el más antiguo hasta el ciclo actual.
    Cuando se provee `db`, el día en que inicia cada ciclo se ancla al día real en que se cobró
    el ingreso más grande de ese período (evitando que variaciones de 1 a 10 días en la fecha de
    cobro del sueldo junten dos sueldos en un mismo ciclo o dejen un ciclo sin ingreso principal).
    """
    n = max(1, min(24, cantidad_ciclos))
    n_busqueda = max(2, n)
    nominales_reversa: List[Dict[str, Any]] = []
    cursor = hoy

    for idx in range(n_busqueda):
        ini_nom, fin_nom = get_ciclo_fechas(usuario, cursor)
        punto_medio_nom = ini_nom + (fin_nom - ini_nom) // 2
        nominales_reversa.append({
            "indice_atras": idx,
            "ini_nom": ini_nom,
            "fin_nom": fin_nom,
            "punto_medio_nom": punto_medio_nom,
        })
        cursor = ini_nom - timedelta(days=1)

    nominales = list(reversed(nominales_reversa))

    if db is not None and nominales:
        desde_busqueda = nominales[0]["ini_nom"] - timedelta(days=12)
        rows_ing = db.execute(
            select(Transaccion.fecha, Transaccion.monto)
            .where(condicion_ingreso(usuario.id, desde=desde_busqueda, hasta=hoy, hoy=hoy))
        ).all()

        max_ing_global = max((Decimal(str(r.monto)) for r in rows_ing), default=Decimal("0"))
        umbral_principal = max_ing_global * Decimal("0.35")

        fechas_usadas: set[date] = set()
        for item in nominales:
            ini_n: date = item["ini_nom"]
            v_ini = ini_n - timedelta(days=12)
            v_fin = ini_n + timedelta(days=12)
            candidatos = [
                r
                for r in rows_ing
                if v_ini <= r.fecha <= v_fin
                and Decimal(str(r.monto)) >= umbral_principal
                and r.fecha not in fechas_usadas
            ]
            if candidatos and max_ing_global > 0:
                mejor = max(
                    candidatos,
                    key=lambda r: (Decimal(str(r.monto)), -r.fecha.toordinal()),
                )
                item["ini_real"] = mejor.fecha
                fechas_usadas.add(mejor.fecha)
            else:
                item["ini_real"] = ini_n

        # Si hacia el final del último ciclo nominal ya se cobró un nuevo ingreso principal
        # (por ejemplo 1 a 5 días antes de fin_nom), abrimos ese nuevo ciclo en esa fecha.
        ult = nominales[-1]
        v_fin_ult = ult["fin_nom"] - timedelta(days=10)
        if v_fin_ult > ult["ini_real"] + timedelta(days=15) and max_ing_global > 0:
            cand_nuevo = [
                r
                for r in rows_ing
                if v_fin_ult <= r.fecha <= hoy
                and Decimal(str(r.monto)) >= umbral_principal
                and r.fecha not in fechas_usadas
            ]
            if cand_nuevo:
                mejor_nuevo = max(
                    cand_nuevo,
                    key=lambda r: (Decimal(str(r.monto)), -r.fecha.toordinal()),
                )
                nuevo_fin_nom = mejor_nuevo.fecha + timedelta(days=30)
                nominales.append({
                    "indice_atras": 0,
                    "ini_nom": mejor_nuevo.fecha,
                    "fin_nom": nuevo_fin_nom,
                    "ini_real": mejor_nuevo.fecha,
                    "punto_medio_nom": mejor_nuevo.fecha + timedelta(days=15),
                })
    else:
        for item in nominales:
            item["ini_real"] = item["ini_nom"]

    total_items = len(nominales)
    ciclos_out: List[Dict[str, Any]] = []
    for i, item in enumerate(nominales):
        ini_real: date = item["ini_real"]
        if i + 1 < total_items:
            fin_real: date = nominales[i + 1]["ini_real"] - timedelta(days=1)
        else:
            fin_real = max(item["fin_nom"], ini_real)

        if fin_real < ini_real:
            fin_real = ini_real

        punto_medio = ini_real + (fin_real - ini_real) // 2
        mes_txt = MESES_CORTOS.get(punto_medio.month, str(punto_medio.month))
        anio_corto = str(punto_medio.year)[-2:]
        etiqueta = f"{mes_txt} {anio_corto}"
        rango_texto = f"{ini_real.strftime('%d/%m')} al {fin_real.strftime('%d/%m')}"
        idx_atras = (total_items - 1) - i

        ciclos_out.append({
            "indice_atras": idx_atras,
            "clave": f"{punto_medio.year}-{punto_medio.month:02d}-{ini_real.isoformat()}",
            "etiqueta": etiqueta,
            "rango_texto": rango_texto,
            "fecha_inicio": ini_real,
            "fecha_fin": fin_real,
            "es_actual": i == (total_items - 1),
        })

    return ciclos_out[-n:]


def _resolver_cotizacion_valor(cotizacion_dict: Dict[str, Any]) -> Decimal:
    venta = cotizacion_dict.get("venta") or cotizacion_dict.get("promedio") or cotizacion_dict.get("compra")
    try:
        if venta is not None and float(venta) > 0:
            return Decimal(str(venta))
    except (ValueError, TypeError):
        pass
    return Decimal("1200")


def _convertir_monto(
    monto_ars: Decimal,
    monto_usd: Decimal,
    moneda_modo: str,
    tc: Decimal,
) -> Decimal:
    modo = (moneda_modo or "solo_ars").lower()
    if modo == "solo_usd":
        return monto_usd
    if modo == "consolidado_ars":
        return monto_ars + (monto_usd * tc)
    if modo == "consolidado_usd":
        return monto_usd + (monto_ars / tc if tc > 0 else Decimal("0"))
    return monto_ars


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

    cotizacion_info = get_cotizacion_usuario(usuario)
    tc_valor = _resolver_cotizacion_valor(cotizacion_info)

    ciclos_con_prev = generar_ventana_ciclos(usuario, ref_hoy, max(2, cantidad_ciclos), db=db)
    ciclos = ciclos_con_prev[-max(1, min(24, cantidad_ciclos)):]
    fecha_inicio_global = ciclos[0]["fecha_inicio"]
    fecha_fin_global = min(ciclos[-1]["fecha_fin"], ref_hoy)

    # ── 1. Serie por Ciclo (Ingresos, Gastos, Balance, Tasa de ahorro) ────────
    # Consultamos gastos e ingresos de toda la ventana en 2 queries agrupadas por fecha y moneda
    # para evitar N round-trips por ciclo.
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
        )
        .where(cond_ventana_gasto)
        .group_by(
            Transaccion.fecha,
            Transaccion.moneda,
            Transaccion.categoria_id,
            Transaccion.subcategoria_id,
        )
    ).all()

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
        )
        .where(cond_ventana_ingreso)
        .group_by(Transaccion.fecha, Transaccion.moneda)
    ).all()

    serie_ciclos_raw: List[Dict[str, Any]] = []
    suma_gastos_ventana = Decimal("0")
    suma_ingresos_ventana = Decimal("0")

    for c in ciclos:
        c_ini: date = c["fecha_inicio"]
        c_fin: date = min(c["fecha_fin"], ref_hoy)
        por_cat_dec: Dict[str, Decimal] = {}
        por_sub_dec: Dict[str, Decimal] = {}

        if c_ini > ref_hoy:
            gasto_ciclo = Decimal("0")
            ingreso_ciclo = Decimal("0")
        else:
            gasto_ciclo = Decimal("0")
            for f_tx, mon_tx, cat_tx, sub_tx, monto_tx in rows_g_diarios:
                if c_ini <= f_tx <= c_fin:
                    m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
                    m_dec = Decimal(str(monto_tx))
                    m_conv = _convertir_monto(
                        m_dec if m_code == "ARS" else Decimal("0"),
                        m_dec if m_code == "USD" else Decimal("0"),
                        modo,
                        tc_valor,
                    )
                    if m_conv <= 0:
                        continue
                    gasto_ciclo += m_conv
                    cid_key = str(cat_tx) if cat_tx else "sin-categoria"
                    por_cat_dec[cid_key] = por_cat_dec.get(cid_key, Decimal("0")) + m_conv
                    sid_key = str(sub_tx) if sub_tx else "general"
                    por_sub_dec[sid_key] = por_sub_dec.get(sid_key, Decimal("0")) + m_conv
                    por_sub_dec[f"{cid_key}:{sid_key}"] = (
                        por_sub_dec.get(f"{cid_key}:{sid_key}", Decimal("0")) + m_conv
                    )

            i_ars = Decimal("0")
            i_usd = Decimal("0")
            for f_tx, mon_tx, monto_tx in rows_i_diarios:
                if c_ini <= f_tx <= c_fin:
                    m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
                    if m_code == "USD":
                        i_usd += Decimal(str(monto_tx))
                    else:
                        i_ars += Decimal(str(monto_tx))
            ingreso_ciclo = _convertir_monto(i_ars, i_usd, modo, tc_valor)

        balance_ciclo = ingreso_ciclo - gasto_ciclo
        tasa_ahorro = (
            max(-100.0, min(100.0, round(float((balance_ciclo / ingreso_ciclo) * Decimal("100")), 1)))
            if ingreso_ciclo > 0
            else 0.0
        )

        suma_gastos_ventana += gasto_ciclo
        suma_ingresos_ventana += ingreso_ciclo

        serie_ciclos_raw.append({
            "clave": c["clave"],
            "etiqueta": c["etiqueta"],
            "rango_texto": c["rango_texto"],
            "fecha_inicio": c["fecha_inicio"].isoformat(),
            "fecha_fin": c["fecha_fin"].isoformat(),
            "es_actual": c["es_actual"],
            "ingresos": round(float(ingreso_ciclo), 2),
            "gastos": round(float(gasto_ciclo), 2),
            "balance": round(float(balance_ciclo), 2),
            "tasa_ahorro_pct": tasa_ahorro,
            "por_categoria": {k: round(float(v), 2) for k, v in por_cat_dec.items()},
            "por_subcategoria": {k: round(float(v), 2) for k, v in por_sub_dec.items()},
        })

    cant_ciclos_efectivos = max(1, len(serie_ciclos_raw))
    promedio_gasto_ciclo = suma_gastos_ventana / Decimal(str(cant_ciclos_efectivos))
    promedio_ingreso_ciclo = suma_ingresos_ventana / Decimal(str(cant_ciclos_efectivos))
    promedio_gasto_float = round(float(promedio_gasto_ciclo), 2)

    serie_ciclos = [
        {**item, "promedio_historico_gastos": promedio_gasto_float}
        for item in serie_ciclos_raw
    ]

    # Variación del último ciclo vs el anterior inmediato
    variacion_gasto_vs_ant: Optional[float] = None
    variacion_ingreso_vs_ant: Optional[float] = None

    if len(serie_ciclos) >= 2:
        ult = Decimal(str(serie_ciclos[-1]["gastos"]))
        prev = Decimal(str(serie_ciclos[-2]["gastos"]))
        if prev > 0:
            variacion_gasto_vs_ant = round(float(((ult - prev) / prev) * Decimal("100")), 1)

        ult_i = Decimal(str(serie_ciclos[-1]["ingresos"]))
        prev_i = Decimal(str(serie_ciclos[-2]["ingresos"]))
        if prev_i > 0:
            variacion_ingreso_vs_ant = round(float(((ult_i - prev_i) / prev_i) * Decimal("100")), 1)
    else:
        # Si pidió 1 solo ciclo, usamos el ciclo inmediato anterior anclado por ingreso principal
        ciclo_prev = ciclos_con_prev[-2] if len(ciclos_con_prev) >= 2 else ciclos[0]
        ini_ant, fin_ant = ciclo_prev["fecha_inicio"], ciclo_prev["fecha_fin"]
        cond_g_ant = _aplicar_filtros_extra(
            condicion_gasto(usuario.id, desde=ini_ant, hasta=min(fin_ant, ref_hoy), hoy=ref_hoy),
            billetera_id=billetera_id,
            tarjeta_id=tarjeta_id,
            categoria_id=categoria_id,
            naturaleza=naturaleza,
        )
        rows_g_ant = db.execute(
            select(Transaccion.moneda, func.coalesce(func.sum(Transaccion.monto), 0))
            .where(cond_g_ant)
            .group_by(Transaccion.moneda)
        ).all()
        map_g_ant = {
            (r[0].value if hasattr(r[0], "value") else str(r[0])): Decimal(str(r[1]))
            for r in rows_g_ant
        }
        gasto_ant = _convertir_monto(
            map_g_ant.get("ARS", Decimal("0")),
            map_g_ant.get("USD", Decimal("0")),
            modo,
            tc_valor,
        )
        if gasto_ant > 0:
            variacion_gasto_vs_ant = round(
                float(((suma_gastos_ventana - gasto_ant) / gasto_ant) * Decimal("100")),
                1,
            )

    # ── 2. Desglose por Categoría y Subcategoría en la ventana seleccionada ───
    cond_ventana_gasto = _aplicar_filtros_extra(
        condicion_gasto(usuario.id, desde=fecha_inicio_global, hasta=fecha_fin_global, hoy=ref_hoy),
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=categoria_id,
        naturaleza=naturaleza,
    )

    stmt_desglose = (
        select(
            Transaccion.categoria_id,
            Categoria.nombre.label("categoria_nombre"),
            Transaccion.subcategoria_id,
            Subcategoria.nombre.label("subcategoria_nombre"),
            Transaccion.moneda,
            func.coalesce(func.sum(Transaccion.monto), 0).label("total_monto"),
            func.count(Transaccion.id).label("cant_tx"),
        )
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
        .where(cond_ventana_gasto)
        .group_by(
            Transaccion.categoria_id,
            Categoria.nombre,
            Transaccion.subcategoria_id,
            Subcategoria.nombre,
            Transaccion.moneda,
        )
    )
    rows_desglose = db.execute(stmt_desglose).all()

    # Agrupación jerárquica Categoría -> Subcategoría
    cat_acum: Dict[str, Dict[str, Any]] = {}
    total_operaciones_gasto = 0

    for row in rows_desglose:
        cid_str = str(row.categoria_id) if row.categoria_id else "sin-categoria"
        cnombre = row.categoria_nombre or "Otros"
        sid_str = str(row.subcategoria_id) if row.subcategoria_id else "general"
        snombre = row.subcategoria_nombre or "Otros"
        mon_str = row.moneda.value if hasattr(row.moneda, "value") else str(row.moneda)
        monto_dec = Decimal(str(row.total_monto or 0))
        cant = int(row.cant_tx or 0)

        monto_conv = _convertir_monto(
            monto_dec if mon_str == "ARS" else Decimal("0"),
            monto_dec if mon_str == "USD" else Decimal("0"),
            modo,
            tc_valor,
        )
        if monto_conv <= 0:
            continue

        total_operaciones_gasto += cant

        if cid_str not in cat_acum:
            cat_acum[cid_str] = {
                "categoria_id": str(row.categoria_id) if row.categoria_id else None,
                "categoria_nombre": cnombre,
                "monto": Decimal("0"),
                "cantidad_movimientos": 0,
                "subs": {},
            }

        cat_entry = cat_acum[cid_str]
        cat_entry["monto"] += monto_conv
        cat_entry["cantidad_movimientos"] += cant

        subs_map = cat_entry["subs"]
        if sid_str not in subs_map:
            subs_map[sid_str] = {
                "subcategoria_id": sid_str,
                "subcategoria_nombre": snombre,
                "monto": Decimal("0"),
                "cantidad_movimientos": 0,
            }
        subs_map[sid_str]["monto"] += monto_conv
        subs_map[sid_str]["cantidad_movimientos"] += cant

    # Ordenar categorías de mayor a menor monto
    cats_ordenadas = sorted(cat_acum.values(), key=lambda x: -x["monto"])
    max_cat_monto = cats_ordenadas[0]["monto"] if cats_ordenadas else Decimal("0")

    desglose_categorias: List[Dict[str, Any]] = []
    for c_item in cats_ordenadas:
        c_monto: Decimal = c_item["monto"]
        pct_total = (
            round(float((c_monto / suma_gastos_ventana) * Decimal("100")), 1)
            if suma_gastos_ventana > 0
            else 0.0
        )
        ancho_barra = (
            round(float((c_monto / max_cat_monto) * Decimal("100")), 1)
            if max_cat_monto > 0
            else 0.0
        )

        subs_ordenadas = sorted(c_item["subs"].values(), key=lambda s: -s["monto"])
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
                round(float((s_monto / suma_gastos_ventana) * Decimal("100")), 1)
                if suma_gastos_ventana > 0
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

    # ── 3. Generación de Insights Inteligentes (Deterministas) ────────────────
    insights: List[Dict[str, Any]] = []

    if desglose_categorias:
        top_cat = desglose_categorias[0]
        insights.append({
            "id": "top-categoria",
            "tipo": "info",
            "titulo": f"{top_cat['categoria_nombre']} lidera tus gastos ({top_cat['porcentaje_del_total']}%)",
            "descripcion": f"Concentra {top_cat['cantidad_movimientos']} movimientos en el período seleccionado.",
        })

    if variacion_gasto_vs_ant is not None:
        if variacion_gasto_vs_ant <= -5:
            insights.append({
                "id": "variacion-gasto",
                "tipo": "positivo",
                "titulo": f"Bajaste tus gastos un {abs(variacion_gasto_vs_ant)}% vs. el ciclo anterior",
                "descripcion": "Buen ritmo de contención respecto al ciclo previo.",
            })
        elif variacion_gasto_vs_ant >= 10:
            insights.append({
                "id": "variacion-gasto",
                "tipo": "alerta",
                "titulo": f"Tus gastos subieron un {variacion_gasto_vs_ant}% respecto al ciclo pasado",
                "descripcion": "Revisá el desglose por categorías para identificar el desvío.",
            })

    balance_ventana = suma_ingresos_ventana - suma_gastos_ventana
    tasa_ahorro_ventana = (
        round(float((balance_ventana / suma_ingresos_ventana) * Decimal("100")), 1)
        if suma_ingresos_ventana > 0
        else 0.0
    )

    if suma_ingresos_ventana > 0:
        if tasa_ahorro_ventana >= 20:
            insights.append({
                "id": "tasa-ahorro",
                "tipo": "positivo",
                "titulo": f"Tasa de ahorro sólida del {tasa_ahorro_ventana}%",
                "descripcion": "Estás reteniendo más de una quinta parte de tus ingresos en el período.",
            })
        elif tasa_ahorro_ventana < 0:
            insights.append({
                "id": "tasa-ahorro",
                "tipo": "alerta",
                "titulo": "Flujo neto negativo en el período evaluado",
                "descripcion": "Los egresos superaron a los ingresos registrados en esta ventana.",
            })

    # ── 4. Catálogo de filtros disponibles (Billeteras y Tarjetas activas) ────
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
        "cotizacion_referencia": {
            "tipo": cotizacion_info.get("tipo", "blue"),
            "nombre": cotizacion_info.get("nombre", "Dólar Blue"),
            "valor": float(tc_valor),
            "fecha_actualizacion": cotizacion_info.get("fecha_actualizacion"),
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
            "ingresos_totales": round(float(suma_ingresos_ventana), 2),
            "gastos_totales": round(float(suma_gastos_ventana), 2),
            "balance_neto": round(float(balance_ventana), 2),
            "tasa_ahorro_pct": tasa_ahorro_ventana,
            "promedio_gasto_ciclo": promedio_gasto_float,
            "promedio_ingreso_ciclo": round(float(promedio_ingreso_ciclo), 2),
            "variacion_gasto_vs_anterior_pct": variacion_gasto_vs_ant,
            "variacion_ingreso_vs_anterior_pct": variacion_ingreso_vs_ant,
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
    ciclos_con_prev = generar_ventana_ciclos(usuario, ref_hoy, max(2, cantidad_ciclos), db=db)
    ciclos = ciclos_con_prev[-max(1, min(24, cantidad_ciclos)):]
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

    # Mapear número de cuota si pertenece a grupo_cuotas
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
    Compara mano a mano dos ciclos financieros (por defecto: Ciclo actual vs. Ciclo anterior),
    devolviendo totales, deltas porcentuales y el espejo categoría por categoría.
    """
    ref_hoy = hoy or hoy_argentina()
    modo = (moneda_modo or "solo_ars").lower()
    if modo not in ("solo_ars", "solo_usd", "consolidado_ars", "consolidado_usd"):
        modo = "solo_ars"

    moneda_visual = "USD" if modo in ("solo_usd", "consolidado_usd") else "ARS"
    cotizacion_info = get_cotizacion_usuario(usuario)
    tc_valor = _resolver_cotizacion_valor(cotizacion_info)

    catalogo_ciclos = generar_ventana_ciclos(usuario, ref_hoy, 12, db=db)
    mapa_ciclos = {c["clave"]: c for c in catalogo_ciclos}

    ciclo_a = mapa_ciclos.get(ciclo_a_clave or "") or catalogo_ciclos[-1]
    ciclo_b_default = catalogo_ciclos[-2] if len(catalogo_ciclos) >= 2 else catalogo_ciclos[-1]
    ciclo_b = mapa_ciclos.get(ciclo_b_clave or "") or ciclo_b_default

    def _agrupar_ciclo(ciclo_meta: Dict[str, Any]):
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
                Transaccion.categoria_id,
                Categoria.nombre.label("categoria_nombre"),
                Transaccion.moneda,
                func.coalesce(func.sum(Transaccion.monto), 0).label("total_monto"),
            )
            .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
            .where(cond_g)
            .group_by(Transaccion.categoria_id, Categoria.nombre, Transaccion.moneda)
        ).all()

        por_cat: Dict[str, Dict[str, Any]] = {}
        gasto_total = Decimal("0")
        for r in rows_cat:
            cid = str(r.categoria_id) if r.categoria_id else "sin-categoria"
            cnombre = r.categoria_nombre or "Otros"
            mon_code = r.moneda.value if hasattr(r.moneda, "value") else str(r.moneda)
            m_dec = Decimal(str(r.total_monto or 0))
            m_conv = _convertir_monto(
                m_dec if mon_code == "ARS" else Decimal("0"),
                m_dec if mon_code == "USD" else Decimal("0"),
                modo,
                tc_valor,
            )
            if m_conv <= 0:
                continue
            gasto_total += m_conv
            if cid not in por_cat:
                por_cat[cid] = {
                    "categoria_id": str(r.categoria_id) if r.categoria_id else None,
                    "categoria_nombre": cnombre,
                    "monto": Decimal("0"),
                }
            por_cat[cid]["monto"] += m_conv

        cond_i_base = condicion_ingreso(usuario.id, desde=c_ini, hasta=c_fin, hoy=ref_hoy)
        cond_i = (
            and_(cond_i_base, Transaccion.billetera_id == billetera_id)
            if billetera_id
            else cond_i_base
        )
        rows_i = db.execute(
            select(Transaccion.moneda, func.coalesce(func.sum(Transaccion.monto), 0))
            .where(cond_i)
            .group_by(Transaccion.moneda)
        ).all()
        map_i = {
            (r[0].value if hasattr(r[0], "value") else str(r[0])): Decimal(str(r[1]))
            for r in rows_i
        }
        ingreso_total = _convertir_monto(
            map_i.get("ARS", Decimal("0")),
            map_i.get("USD", Decimal("0")),
            modo,
            tc_valor,
        )
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
        if ingreso_b > 0
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
            }
            for c in reversed(catalogo_ciclos)
        ],
        "ciclo_a": {
            "clave": ciclo_a["clave"],
            "etiqueta": ciclo_a["etiqueta"],
            "rango_texto": ciclo_a["rango_texto"],
        },
        "ciclo_b": {
            "clave": ciclo_b["clave"],
            "etiqueta": ciclo_b["etiqueta"],
            "rango_texto": ciclo_b["rango_texto"],
        },
        "resumen": {
            "gasto_a": round(float(gasto_a), 2),
            "gasto_b": round(float(gasto_b), 2),
            "diferencia_gasto_monto": round(float(diff_gasto_total), 2),
            "diferencia_gasto_pct": diff_gasto_pct,
            "ingreso_a": round(float(ingreso_a), 2),
            "ingreso_b": round(float(ingreso_b), 2),
            "diferencia_ingreso_monto": round(float(diff_ingreso_total), 2),
            "diferencia_ingreso_pct": diff_ingreso_pct,
            "balance_a": round(float(ingreso_a - gasto_a), 2),
            "balance_b": round(float(ingreso_b - gasto_b), 2),
            "mayor_aumento": mayor_aumento,
            "mayor_ahorro": mayor_ahorro,
        },
        "categorias": categorias_out,
    }


# ── Fase 3, 4 y 5: Re-exportación de Proyección, Informes y Excel (.xlsx) ─────
from app.services.analisis_informes_service import (
    actualizar_informe_usuario,
    crear_informe_usuario,
    eliminar_informe_usuario,
    generar_excel_analisis,
    listar_informes_usuario,
    obtener_proyeccion_compromisos,
)

__all__ = [
    "generar_ventana_ciclos",
    "obtener_resumen_analisis",
    "obtener_movimientos_drilldown",
    "obtener_comparativa_ciclos",
    "obtener_proyeccion_compromisos",
    "listar_informes_usuario",
    "crear_informe_usuario",
    "actualizar_informe_usuario",
    "eliminar_informe_usuario",
    "generar_excel_analisis",
]


