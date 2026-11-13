from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, selectinload

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.grupo_cuotas import GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.analisis_ciclos_service import (
    aplicar_filtros_extra,
    calcular_promedios_y_tasa,
    calcular_serie_ciclos,
    convertir_movimiento,
    generar_ventana_ciclos,
    obtener_mapa_cotizaciones,
    sumar_periodo,
)
from app.services.analisis_insights_service import armar_insights
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.utils.fecha import hoy_argentina


def _consultar_ventana(
    db: Session,
    usuario: Usuario,
    fecha_inicio_global: date,
    fecha_fin_global: date,
    ref_hoy: date,
    modo: str,
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
    categoria_id: Optional[UUID],
    naturaleza: Optional[str],
    solo_gastos: bool,
) -> tuple[List[Any], List[Any], Dict[date, Optional[Decimal]], Any]:
    cond_ventana_gasto = aplicar_filtros_extra(
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

    rows_i_diarios: List[Any] = []
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

    fechas_a_convertir: set[date] = set()
    target_mon = "USD" if modo == "consolidado_ars" else ("ARS" if modo == "consolidado_usd" else None)
    if target_mon:
        for r in list(rows_g_diarios) + list(rows_i_diarios):
            m_code = r[1].value if hasattr(r[1], "value") else str(r[1])
            if m_code.upper() == target_mon:
                fechas_a_convertir.add(r[0])

    cotizaciones_map = obtener_mapa_cotizaciones(db, modo, fechas_a_convertir)
    return rows_g_diarios, rows_i_diarios, cotizaciones_map, cond_ventana_gasto


def _variacion_vs_anterior(
    db: Session,
    usuario: Usuario,
    ciclos: List[Dict[str, Any]],
    ciclos_con_prev: List[Dict[str, Any]],
    serie_ciclos_raw: List[Dict[str, Any]],
    ref_hoy: date,
    modo: str,
    billetera_id: Optional[UUID],
    tarjeta_id: Optional[UUID],
    categoria_id: Optional[UUID],
    naturaleza: Optional[str],
    solo_gastos: bool,
) -> tuple[Optional[float], Optional[float], Optional[str], bool]:
    variacion_gasto_vs_ant: Optional[float] = None
    variacion_ingreso_vs_ant: Optional[float] = None
    variacion_texto: Optional[str] = None

    ciclo_ult_meta = ciclos[-1]
    es_ult_en_curso = (ciclo_ult_meta["fecha_fin"] >= ref_hoy)
    ciclo_ant_meta = ciclos_con_prev[-2] if len(ciclos_con_prev) >= 2 else None

    if ciclo_ant_meta is not None:
        ant_ini = ciclo_ant_meta["fecha_inicio"]
        if es_ult_en_curso:
            dias_n = (ref_hoy - ciclo_ult_meta["fecha_inicio"]).days + 1
            ant_fin = min(ant_ini + timedelta(days=dias_n - 1), ciclo_ant_meta["fecha_fin"])
            variacion_texto = f"Comparado con los mismos {dias_n} días del ciclo anterior"
        else:
            ant_fin = ciclo_ant_meta["fecha_fin"]
            variacion_texto = "Comparado con el ciclo anterior completo"

        gasto_actual = serie_ciclos_raw[-1]["_gasto_dec"]
        ingreso_actual = serie_ciclos_raw[-1]["_ingreso_dec"]

        gasto_ant, ingreso_ant = sumar_periodo(
            db, usuario.id, ant_ini, ant_fin, ref_hoy, modo,
            billetera_id, tarjeta_id, categoria_id, naturaleza, solo_gastos
        )

        if gasto_ant > 0:
            variacion_gasto_vs_ant = round(
                float(((gasto_actual - gasto_ant) / gasto_ant) * Decimal("100")),
                1,
            )

        if not solo_gastos and ingreso_actual is not None and ingreso_ant and ingreso_ant > 0:
            variacion_ingreso_vs_ant = round(
                float(((ingreso_actual - ingreso_ant) / ingreso_ant) * Decimal("100")),
                1,
            )

    return variacion_gasto_vs_ant, variacion_ingreso_vs_ant, variacion_texto, es_ult_en_curso


def _desglose_categorias(
    db: Session,
    cond_ventana_gasto: Any,
    modo: str,
) -> List[Dict[str, Any]]:
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
    cots_desglose = obtener_mapa_cotizaciones(db, modo, fechas_desglose)

    cat_acum: Dict[str, Dict[str, Any]] = {}
    for r in rows_desglose:
        f_tx, mon_tx, cat_id, cnombre, sub_id, snombre, monto_tx, cant_tx = r
        m_code = mon_tx.value if hasattr(mon_tx, "value") else str(mon_tx)
        m_conv, _ = convertir_movimiento(Decimal(str(monto_tx)), m_code, f_tx, modo, cots_desglose)
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

    cats_validas = [c for c in cat_acum.values() if c["monto"] > Decimal("0")]
    cats_ordenadas = sorted(cats_validas, key=lambda x: -x["monto"])
    suma_cats_listadas = sum((c["monto"] for c in cats_ordenadas), Decimal("0"))
    max_cat_monto = cats_ordenadas[0]["monto"] if cats_ordenadas else Decimal("0")

    desglose_categorias: List[Dict[str, Any]] = []
    for c_item in cats_ordenadas:
        c_monto: Decimal = c_item["monto"]
        pct_total = round(float((c_monto / suma_cats_listadas) * Decimal("100")), 1) if suma_cats_listadas > 0 else 0.0
        ancho_barra = round(float((c_monto / max_cat_monto) * Decimal("100")), 1) if max_cat_monto > 0 else 0.0

        subs_validas = [s for s in c_item["subs"].values() if s["monto"] > Decimal("0")]
        subs_ordenadas = sorted(subs_validas, key=lambda s: -s["monto"])
        max_sub_monto = subs_ordenadas[0]["monto"] if subs_ordenadas else Decimal("0")

        subcategorias_out: List[Dict[str, Any]] = []
        for s_item in subs_ordenadas:
            s_monto: Decimal = s_item["monto"]
            subcategorias_out.append({
                "subcategoria_id": s_item["subcategoria_id"],
                "subcategoria_nombre": s_item["subcategoria_nombre"],
                "monto": round(float(s_monto), 2),
                "porcentaje_categoria": round(float((s_monto / c_monto) * Decimal("100")), 1) if c_monto > 0 else 0.0,
                "porcentaje_total": round(float((s_monto / suma_cats_listadas) * Decimal("100")), 1) if suma_cats_listadas > 0 else 0.0,
                "ancho_barra_pct": round(float((s_monto / max_sub_monto) * Decimal("100")), 1) if max_sub_monto > 0 else 0.0,
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

    return desglose_categorias


def _filtros_disponibles(db: Session, usuario_id: UUID) -> Dict[str, Any]:
    billeteras_activas = (
        db.execute(
            select(Billetera)
            .where(
                Billetera.usuario_id == usuario_id,
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
            .where(TarjetaCredito.usuario_id == usuario_id)
            .order_by(TarjetaCredito.nombre.asc())
        )
        .scalars()
        .all()
    )
    return {
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
    }


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
    """
    Orquesta la construcción del resumen completo de Análisis financiero.
    """
    ref_hoy = hoy or hoy_argentina()
    modo = (moneda_modo or "solo_ars").lower()
    if modo not in ("solo_ars", "solo_usd", "consolidado_ars", "consolidado_usd"):
        modo = "solo_ars"

    moneda_visual = "USD" if modo in ("solo_usd", "consolidado_usd") else "ARS"
    solo_gastos = bool(categoria_id or tarjeta_id)

    ciclos_con_prev = generar_ventana_ciclos(usuario, ref_hoy, max(2, cantidad_ciclos + 1))
    cant_n = max(1, min(24, cantidad_ciclos))
    ciclos = ciclos_con_prev[-cant_n:]
    fecha_inicio_global = ciclos[0]["fecha_inicio"]
    fecha_fin_global = min(ciclos[-1]["fecha_fin"], ref_hoy)

    rows_g, rows_i, cotizaciones_map, cond_gasto = _consultar_ventana(
        db, usuario, fecha_inicio_global, fecha_fin_global, ref_hoy,
        modo, billetera_id, tarjeta_id, categoria_id, naturaleza, solo_gastos
    )

    serie_raw, movs_sin_cot, cant_ops = calcular_serie_ciclos(
        ciclos, ref_hoy, modo, solo_gastos, rows_g, rows_i, cotizaciones_map
    )

    ciclos_comp, prom_gasto, prom_ingreso, tasa_ventana, serie_ciclos = calcular_promedios_y_tasa(
        serie_raw, ref_hoy, solo_gastos
    )

    var_gasto, var_ingreso, var_texto, es_ult_en_curso = _variacion_vs_anterior(
        db, usuario, ciclos, ciclos_con_prev, serie_raw, ref_hoy,
        modo, billetera_id, tarjeta_id, categoria_id, naturaleza, solo_gastos
    )

    desglose = _desglose_categorias(db, cond_gasto, modo)

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

    insights = armar_insights(
        desglose_categorias=desglose,
        variacion_gasto_vs_ant=var_gasto,
        es_ult_en_curso=es_ult_en_curso,
        ciclos_completos=ciclos_comp,
        tasa_ahorro_ventana=tasa_ventana,
        modo=modo,
        solo_gastos=solo_gastos,
    )

    filtros = _filtros_disponibles(db, usuario.id)

    aviso_cotizacion: Optional[str] = None
    if movs_sin_cot > 0:
        if modo == "consolidado_ars":
            if movs_sin_cot == 1:
                aviso_cotizacion = "1 movimiento en dólares no se pudo pasar a pesos porque falta la cotización de ese día."
            else:
                aviso_cotizacion = f"{movs_sin_cot} movimientos en dólares no se pudieron pasar a pesos porque falta la cotización de esos días."
        elif modo == "consolidado_usd":
            if movs_sin_cot == 1:
                aviso_cotizacion = "1 movimiento en pesos no se pudo pasar a dólares porque falta la cotización de ese día."
            else:
                aviso_cotizacion = f"{movs_sin_cot} movimientos en pesos no se pudieron pasar a dólares porque falta la cotización de esos días."

    if solo_gastos:
        tasa_ahorro_texto: Optional[str] = None
    elif len(ciclos) == 1:
        tasa_ahorro_texto = "Aparece cuando termine el ciclo."
    elif tasa_ventana is not None and es_ult_en_curso:
        tasa_ahorro_texto = "Sin contar el ciclo en curso."
    else:
        tasa_ahorro_texto = None

    return {
        "moneda_modo": modo,
        "moneda_visual": moneda_visual,
        "movimientos_sin_cotizacion": movs_sin_cot,
        "aviso_cotizacion": aviso_cotizacion,
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
            "tasa_ahorro_pct": tasa_ventana,
            "tasa_ahorro_texto": tasa_ahorro_texto,
            "promedio_gasto_ciclo": prom_gasto,
            "promedio_ingreso_ciclo": prom_ingreso,
            "variacion_gasto_vs_anterior_pct": var_gasto,
            "variacion_ingreso_vs_anterior_pct": var_ingreso,
            "variacion_texto": var_texto,
            "cantidad_operaciones": cant_ops,
        },
        "insights": insights,
        "serie_ciclos": serie_ciclos,
        "desglose_categorias": desglose,
        "filtros_disponibles": filtros,
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
    cond = aplicar_filtros_extra(
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
