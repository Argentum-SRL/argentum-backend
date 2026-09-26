"""
Módulo de Mediciones del Motor Financiero de Argentum.

Contiene las funciones especializadas para la extracción y cálculo determinístico
de cada bloque financiero (B a P) para testingadmin@argentum.com.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.orm import Session, selectinload, joinedload

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import Meta
from app.models.perfil_financiero import PerfilFinanciero
from app.models.presupuesto import Presupuesto
from app.models.suscripcion import Suscripcion
from app.models.tools import IPCCache
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.routers.whatsapp.gastos import (
    ETIQUETAS_PERIODO,
    _formatear_respuesta_gastos,
    _rango_periodo,
)
from app.schemas.tools import InstallmentConvenienceRequest
from app.services import (
    ai_service,
    contexto_financiero_service,
    dashboard_service,
    gastos_consulta_service,
    perfil_financiero_service,
    proyeccion_service,
    suscripcion_service,
    tools_service,
)
from app.services.analisis_financiero_service import (
    _carga,
    _ciclos_anteriores,
    calcular_perfil_nuevo,
)
from app.utils.finanzas import clasificar_gastos, es_gasto_consumo
from app.utils.formato import formatear_monto


def _fmt_monto(val: Any) -> str:
    """Convierte un valor numérico a texto con 2 decimales para formato plano."""
    if val is None:
        return "0.00"
    if isinstance(val, (Decimal, float, int)):
        return f"{float(val):.2f}"
    try:
        return f"{float(str(val)):.2f}"
    except (ValueError, TypeError):
        return str(val)


def _fmt_val(val: Any) -> str:
    """Formatea valores generales a string limpio y determinístico."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (Decimal, float)):
        return f"{float(val):.2f}"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return str(val).strip()


def medir_bloque_b(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque B: Billeteras activas y totales consolidados por moneda."""
    res = {}
    billeteras = (
        db.query(Billetera)
        .filter(
            Billetera.usuario_id == usuario.id,
            Billetera.estado == EstadoBilletera.ACTIVA,
        )
        .order_by(Billetera.nombre.asc())
        .all()
    )

    tot_ars_con_inv = Decimal("0")
    tot_ars_sin_inv = Decimal("0")
    tot_usd_con_inv = Decimal("0")
    tot_usd_sin_inv = Decimal("0")

    res["billeteras.cantidad_activas"] = str(len(billeteras))
    for b in billeteras:
        prefix = f"billeteras.{b.nombre.lower().replace(' ', '_')}"
        res[f"{prefix}.id"] = str(b.id)
        res[f"{prefix}.nombre"] = b.nombre
        res[f"{prefix}.moneda"] = b.moneda.value
        res[f"{prefix}.es_inversion"] = _fmt_val(b.es_inversion)
        res[f"{prefix}.es_efectivo"] = _fmt_val(b.es_efectivo)
        res[f"{prefix}.saldo"] = _fmt_monto(b.saldo_actual)

        saldo = b.saldo_actual or Decimal("0")
        if b.moneda == Moneda.ARS:
            tot_ars_con_inv += saldo
            if not b.es_inversion:
                tot_ars_sin_inv += saldo
        elif b.moneda == Moneda.USD:
            tot_usd_con_inv += saldo
            if not b.es_inversion:
                tot_usd_sin_inv += saldo

    res["billeteras.total_ars_con_inversion"] = _fmt_monto(tot_ars_con_inv)
    res["billeteras.total_ars_sin_inversion"] = _fmt_monto(tot_ars_sin_inv)
    res["billeteras.total_usd_con_inversion"] = _fmt_monto(tot_usd_con_inv)
    res["billeteras.total_usd_sin_inversion"] = _fmt_monto(tot_usd_sin_inv)
    return res


def medir_bloque_c(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque C: Saldo disponible canónico y desglose de componentes."""
    res = {}
    disp = contexto_financiero_service._calcular_saldo_disponible_sync(
        db, usuario.id
    )
    for m in ["ars", "usd"]:
        d_m = disp.get(m, {})
        res[f"saldo_disponible.{m}.total_billeteras"] = _fmt_monto(
            d_m.get("total_billeteras")
        )
        res[f"saldo_disponible.{m}.cuotas_comprometidas"] = _fmt_monto(
            d_m.get("cuotas_comprometidas")
        )
        res[f"saldo_disponible.{m}.suscripciones_mensuales"] = _fmt_monto(
            d_m.get("suscripciones_mensuales")
        )
        res[f"saldo_disponible.{m}.saldo_disponible"] = _fmt_monto(
            d_m.get("saldo_disponible")
        )
    return res


def medir_bloque_d(
    db: Session, usuario: Usuario, hoy: date, ini_act: date, fin_act: date
) -> dict[str, str]:
    """Bloque D: Gasto del ciclo actual según cada componente y desglose de tarjetas."""
    res = {}

    # 1. 'Cuánto gasté' de WhatsApp en el ciclo actual
    res_wpp = gastos_consulta_service.calcular_gastos_periodo(
        db, usuario.id, ini_act, fin_act, top_n=3
    )
    res["gasto_ciclo.whatsapp_consulta.ars_total"] = _fmt_monto(
        res_wpp["ars"]["total"]
    )
    res["gasto_ciclo.whatsapp_consulta.ars_cantidad"] = str(
        res_wpp["ars"]["cantidad"]
    )
    res["gasto_ciclo.whatsapp_consulta.usd_total"] = _fmt_monto(
        res_wpp["usd"]["total"]
    )
    res["gasto_ciclo.whatsapp_consulta.usd_cantidad"] = str(
        res_wpp["usd"]["cantidad"]
    )

    # 2. Resumen del dashboard (Query 1 egresos del ciclo)
    cycle_actual_cond = and_(
        Transaccion.fecha >= ini_act, Transaccion.fecha <= fin_act
    )
    res_stmt_where = and_(
        Transaccion.usuario_id == usuario.id,
        Transaccion.es_padre_cuotas == False,
        Transaccion.metodo_pago.is_distinct_from(MetodoPago.CREDITO),
        Transaccion.movimiento_meta_id.is_(None),
        ~Transaccion.descripcion.ilike("Aporte a la meta:%"),
        ~Transaccion.descripcion.ilike("Retiro de la meta:%"),
        or_(
            Transaccion.estado_verificacion
            == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.estado_verificacion == None,
        ),
    )
    q_res = select(
        func.sum(
            case(
                (
                    and_(
                        cycle_actual_cond,
                        Transaccion.moneda == Moneda.ARS,
                        Transaccion.tipo == TipoTransaccion.EGRESO,
                    ),
                    Transaccion.monto,
                ),
                else_=0,
            )
        ).label("egr_actual_ars"),
        func.sum(
            case(
                (
                    and_(
                        cycle_actual_cond,
                        Transaccion.moneda == Moneda.USD,
                        Transaccion.tipo == TipoTransaccion.EGRESO,
                    ),
                    Transaccion.monto,
                ),
                else_=0,
            )
        ).label("egr_actual_usd"),
    ).where(res_stmt_where)
    row_dash = db.execute(q_res).one()
    res["gasto_ciclo.dashboard_resumen.ars_egresos"] = _fmt_monto(
        row_dash.egr_actual_ars
    )
    res["gasto_ciclo.dashboard_resumen.usd_egresos"] = _fmt_monto(
        row_dash.egr_actual_usd
    )

    # 3. Gastos por categoría (cat_where en dashboard_service)
    cat_where = and_(
        Transaccion.usuario_id == usuario.id,
        Transaccion.fecha >= ini_act,
        Transaccion.fecha <= fin_act,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.es_padre_cuotas == False,
        Transaccion.metodo_pago.is_distinct_from(MetodoPago.CREDITO),
        Transaccion.movimiento_meta_id.is_(None),
        ~Transaccion.descripcion.ilike("Aporte a la meta:%"),
        ~Transaccion.descripcion.ilike("Retiro de la meta:%"),
        or_(
            Transaccion.estado_verificacion
            == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.estado_verificacion == None,
        ),
    )
    cat_stmt = (
        select(
            Categoria.nombre.label("categoria_nombre"),
            Transaccion.moneda,
            func.sum(Transaccion.monto).label("total"),
        )
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .where(cat_where)
        .group_by(Categoria.nombre, Transaccion.moneda)
        .order_by(func.sum(Transaccion.monto).desc())
    )
    cat_rows = db.execute(cat_stmt).all()
    cat_tot_ars = Decimal("0")
    cat_tot_usd = Decimal("0")
    for r in cat_rows:
        nombre = (r.categoria_nombre or "General").lower().replace(" ", "_")
        m_str = r.moneda.value.lower()
        res[f"gasto_ciclo.categorias.{m_str}.{nombre}"] = _fmt_monto(r.total)
        if r.moneda == Moneda.ARS:
            cat_tot_ars += r.total or Decimal("0")
        elif r.moneda == Moneda.USD:
            cat_tot_usd += r.total or Decimal("0")
    res["gasto_ciclo.categorias.ars_total"] = _fmt_monto(cat_tot_ars)
    res["gasto_ciclo.categorias.usd_total"] = _fmt_monto(cat_tot_usd)

    # 4. calcular_balance_ciclo
    bal = dashboard_service.calcular_balance_ciclo(db, usuario)
    res["gasto_ciclo.balance_ciclo.ars_egresos"] = _fmt_monto(
        bal["ars"]["egresos"]
    )
    res["gasto_ciclo.balance_ciclo.usd_egresos"] = _fmt_monto(
        bal["usd"]["egresos"]
    )

    # 5. Suma con es_gasto_consumo
    txs_ciclo = (
        db.query(Transaccion)
        .filter(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha >= ini_act,
            Transaccion.fecha <= fin_act,
            Transaccion.tipo == TipoTransaccion.EGRESO,
        )
        .all()
    )
    txs_consumo = [t for t in txs_ciclo if es_gasto_consumo(t)]
    tot_cons_ars = sum(
        (t.monto for t in txs_consumo if t.moneda == Moneda.ARS), Decimal("0")
    )
    tot_cons_usd = sum(
        (t.monto for t in txs_consumo if t.moneda == Moneda.USD), Decimal("0")
    )
    res["gasto_ciclo.es_gasto_consumo.ars_total"] = _fmt_monto(tot_cons_ars)
    res["gasto_ciclo.es_gasto_consumo.usd_total"] = _fmt_monto(tot_cons_usd)
    res["gasto_ciclo.es_gasto_consumo.cantidad"] = str(len(txs_consumo))

    # 6. Gasto de presupuestos activos
    pres_list = contexto_financiero_service._resumen_presupuestos_activos_sync(
        db, usuario.id
    )
    pres_ars = sum(
        (p["usado"] for p in pres_list if p.get("moneda") == "ARS"), 0.0
    )
    pres_usd = sum(
        (p["usado"] for p in pres_list if p.get("moneda") == "USD"), 0.0
    )
    res["gasto_ciclo.presupuestos.ars_usado_total"] = _fmt_monto(pres_ars)
    res["gasto_ciclo.presupuestos.usd_usado_total"] = _fmt_monto(pres_usd)

    # 7. gasto_promedio_variable de tools_service.obtener_contexto_financiero
    ctx_fin = tools_service.obtener_contexto_financiero(str(usuario.id), db)
    res["gasto_ciclo.contexto_financiero.gasto_promedio_variable"] = _fmt_monto(
        ctx_fin.get("gasto_promedio_variable")
    )

    # 8. Compras con tarjeta del ciclo (padres, cuotas hijas y pagos de resumen)
    padres = (
        db.query(Transaccion)
        .filter(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha >= ini_act,
            Transaccion.fecha <= fin_act,
            Transaccion.es_padre_cuotas == True,
        )
        .all()
    )
    res["gasto_ciclo.tarjeta.padres.cantidad"] = str(len(padres))
    res["gasto_ciclo.tarjeta.padres.ars_total"] = _fmt_monto(
        sum((p.monto for p in padres if p.moneda == Moneda.ARS), Decimal("0"))
    )
    res["gasto_ciclo.tarjeta.padres.usd_total"] = _fmt_monto(
        sum((p.monto for p in padres if p.moneda == Moneda.USD), Decimal("0"))
    )

    cuotas_hijas_ciclo = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.usuario_id == usuario.id,
            Cuota.fecha_vencimiento >= ini_act,
            Cuota.fecha_vencimiento <= fin_act,
        )
        .all()
    )
    res["gasto_ciclo.tarjeta.cuotas_hijas_ciclo.cantidad"] = str(
        len(cuotas_hijas_ciclo)
    )
    tot_cuotas_ars = sum(
        (
            (c.monto_real if c.monto_real is not None else c.monto_proyectado)
            for c in cuotas_hijas_ciclo
            if c.grupo.moneda == Moneda.ARS
        ),
        Decimal("0"),
    )
    tot_cuotas_usd = sum(
        (
            (c.monto_real if c.monto_real is not None else c.monto_proyectado)
            for c in cuotas_hijas_ciclo
            if c.grupo.moneda == Moneda.USD
        ),
        Decimal("0"),
    )
    tot_cuotas_pagadas_ars = sum(
        (
            (c.monto_real if c.monto_real is not None else c.monto_proyectado)
            for c in cuotas_hijas_ciclo
            if c.grupo.moneda == Moneda.ARS and c.pagada
        ),
        Decimal("0"),
    )
    tot_cuotas_impagas_ars = sum(
        (
            (c.monto_real if c.monto_real is not None else c.monto_proyectado)
            for c in cuotas_hijas_ciclo
            if c.grupo.moneda == Moneda.ARS and not c.pagada
        ),
        Decimal("0"),
    )
    res["gasto_ciclo.tarjeta.cuotas_hijas_ciclo.ars_total"] = _fmt_monto(
        tot_cuotas_ars
    )
    res["gasto_ciclo.tarjeta.cuotas_hijas_ciclo.usd_total"] = _fmt_monto(
        tot_cuotas_usd
    )
    res["gasto_ciclo.tarjeta.cuotas_hijas_ciclo.ars_pagadas"] = _fmt_monto(
        tot_cuotas_pagadas_ars
    )
    res["gasto_ciclo.tarjeta.cuotas_hijas_ciclo.ars_impagas"] = _fmt_monto(
        tot_cuotas_impagas_ars
    )

    pagos_resumen = (
        db.query(Transaccion)
        .filter(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha >= ini_act,
            Transaccion.fecha <= fin_act,
            or_(
                Transaccion.pago_resumen_vencimiento.isnot(None),
                Transaccion.descripcion.ilike("%pago%tarjeta%"),
                Transaccion.descripcion.ilike("%pago%resumen%"),
            ),
        )
        .all()
    )
    res["gasto_ciclo.tarjeta.pagos_resumen.cantidad"] = str(len(pagos_resumen))
    res["gasto_ciclo.tarjeta.pagos_resumen.ars_total"] = _fmt_monto(
        sum(
            (p.monto for p in pagos_resumen if p.moneda == Moneda.ARS),
            Decimal("0"),
        )
    )
    return res


def medir_bloque_e(
    db: Session, usuario: Usuario, ini_act: date, fin_act: date
) -> dict[str, str]:
    """Bloque E: Ingresos calculados según cada servicio/pantalla."""
    res = {}
    ctx_fin = tools_service.obtener_contexto_financiero(str(usuario.id), db)
    res["ingreso.contexto_financiero.ingreso_promedio_mensual"] = _fmt_monto(
        ctx_fin.get("ingreso_promedio_mensual")
    )
    res["ingreso.contexto_financiero.ingreso_es_estimacion_parcial"] = _fmt_val(
        ctx_fin.get("ingreso_es_estimacion_parcial")
    )

    perf_calc = calcular_perfil_nuevo(db, usuario)
    res["ingreso.perfil.ingreso_tipico_mensual_ars"] = _fmt_monto(
        perf_calc.get("ingreso_tipico_ars")
    )

    proy = proyeccion_service.calcular_proyeccion(db, usuario)
    res["ingreso.proyeccion.ars_ingresos_proyectados"] = _fmt_monto(
        proy.get("ars", {}).get("ingresos_proyectados")
    )
    res["ingreso.proyeccion.usd_ingresos_proyectados"] = _fmt_monto(
        proy.get("usd", {}).get("ingresos_proyectados")
    )

    bal = dashboard_service.calcular_balance_ciclo(db, usuario)
    res["ingreso.dashboard_balance.ars_ingresos"] = _fmt_monto(
        bal["ars"]["ingresos"]
    )
    res["ingreso.dashboard_balance.usd_ingresos"] = _fmt_monto(
        bal["usd"]["ingresos"]
    )
    return res


def medir_bloque_f(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque F: Perfil financiero calculado, fila guardada y texto IA."""
    res = {}
    p = calcular_perfil_nuevo(db, usuario)
    metricas = [
        "ingreso_tipico_ars",
        "estabilidad_ingreso_mad",
        "ingreso_actual_percentil",
        "gasto_comprometido_ars",
        "gasto_comprometido_ratio",
        "gasto_habitos_ars",
        "gasto_habitos_ratio",
        "capacidad_ahorro",
        "capacidad_ahorro_min",
        "capacidad_ahorro_max",
        "capacidad_ahorro_percentil",
        "gasto_tipico_ars",
        "saldo_disponible_ars",
        "runway_meses",
        "volatilidad_gasto_variable",
        "gasto_actual_percentil",
        "consistencia_registro",
        "cobertura_registro",
        "ciclos_con_datos",
        "ciclos_observados",
    ]
    for m in metricas:
        if m in p:
            res[f"perfil.calculado.{m}"] = _fmt_monto(p[m])
    res["perfil.calculado.nivel_confianza"] = _fmt_val(
        p.get("nivel_confianza")
    )
    res["perfil.calculado.datos_suficientes"] = _fmt_val(
        p.get("datos_suficientes")
    )

    perf_row = (
        db.query(PerfilFinanciero)
        .filter(PerfilFinanciero.usuario_id == usuario.id)
        .first()
    )
    if perf_row:
        res["perfil.guardado.id"] = str(perf_row.id)
        res["perfil.guardado.tasa_ahorro_ars"] = _fmt_monto(
            perf_row.tasa_ahorro_ars
        )
        res["perfil.guardado.tasa_ahorro_usd"] = _fmt_monto(
            perf_row.tasa_ahorro_usd
        )
        res["perfil.guardado.ratio_cuotas_ars"] = _fmt_monto(
            perf_row.ratio_cuotas_ars
        )
        res["perfil.guardado.ratio_cuotas_usd"] = _fmt_monto(
            perf_row.ratio_cuotas_usd
        )
        res["perfil.guardado.consistencia_registro"] = _fmt_monto(
            perf_row.consistencia_registro
        )
        res["perfil.guardado.ultima_actualizacion"] = _fmt_val(
            perf_row.ultima_actualizacion
        )
        res["perfil.texto_contexto_ia"] = (
            perfil_financiero_service.generar_texto_contexto_ia(perf_row)
        )
    else:
        res["perfil.guardado.estado"] = "NO_EXISTE_FILA"
        res["perfil.texto_contexto_ia"] = ""
    return res


def medir_bloque_g(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque G: Proyección aplanada para ARS y USD."""
    res = {}
    proy = proyeccion_service.calcular_proyeccion(db, usuario)

    for moneda in ["ars", "usd"]:
        p_m = proy.get(moneda, {})
        prefix = f"proyeccion.{moneda}"
        res[f"{prefix}.balance_proyectado"] = _fmt_monto(
            p_m.get("balance_proyectado")
        )
        res[f"{prefix}.gasto_proyectado_total"] = _fmt_monto(
            p_m.get("gasto_proyectado_total")
        )
        res[f"{prefix}.ingresos_proyectados"] = _fmt_monto(
            p_m.get("ingresos_proyectados")
        )
        res[f"{prefix}.nivel_confianza"] = _fmt_val(p_m.get("nivel_confianza"))
        res[f"{prefix}.datos_suficientes"] = _fmt_val(
            p_m.get("datos_suficientes")
        )
        res[f"{prefix}.mensaje"] = _fmt_val(p_m.get("mensaje"))

        certezas = p_m.get("certezas") or {}
        res[f"{prefix}.certezas.total"] = _fmt_monto(certezas.get("total"))
        res[f"{prefix}.certezas.cuotas_restantes"] = _fmt_monto(
            certezas.get("cuotas_restantes")
        )
        res[f"{prefix}.certezas.suscripciones_restantes"] = _fmt_monto(
            certezas.get("suscripciones_restantes")
        )
        res[f"{prefix}.certezas.compromisos_restantes"] = _fmt_monto(
            certezas.get("compromisos_restantes")
        )

        desc = p_m.get("descomposicion") or {}
        res[f"{prefix}.descomposicion.cierto"] = _fmt_monto(desc.get("cierto"))
        res[f"{prefix}.descomposicion.recurrente_ya_ocurrido"] = _fmt_monto(
            desc.get("recurrente_ya_ocurrido")
        )
        res[f"{prefix}.descomposicion.variable_proyectado"] = _fmt_monto(
            desc.get("variable_proyectado")
        )

        rango = p_m.get("rango") or {}
        res[f"{prefix}.rango.piso"] = _fmt_monto(rango.get("piso"))
        res[f"{prefix}.rango.central"] = _fmt_monto(rango.get("central"))
        res[f"{prefix}.rango.techo"] = _fmt_monto(rango.get("techo"))

        calib = p_m.get("calibracion") or {}
        res[f"{prefix}.calibracion.pasa_puerta"] = _fmt_val(
            calib.get("pasa_puerta")
        )
        res[f"{prefix}.calibracion.ciclos_evaluados"] = _fmt_val(
            calib.get("ciclos_evaluados")
        )
        res[f"{prefix}.calibracion.cobertura_50"] = _fmt_monto(
            calib.get("cobertura_50")
        )
        res[f"{prefix}.calibracion.cobertura_80"] = _fmt_monto(
            calib.get("cobertura_80")
        )
        res[f"{prefix}.calibracion.cobertura_95"] = _fmt_monto(
            calib.get("cobertura_95")
        )
        res[f"{prefix}.calibracion.ancho_medio_80_rel"] = _fmt_monto(
            calib.get("ancho_medio_80_rel")
        )
    return res


def medir_bloque_h(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque H: Clasificación de gastos (streams recurrentes y clases)."""
    res = {}
    data = _carga(db, usuario)
    hoy = data["hoy"]
    anteriores = _ciclos_anteriores(usuario, hoy, 12)
    comprometidos_externos = [
        *(
            cuota
            for cuota, _ in data["cuotas"]
            if not cuota.pagada and cuota.fecha_vencimiento >= hoy
        ),
        *data["suscripciones"],
    ]
    clasificacion = clasificar_gastos(
        data["txs"], anteriores, data["ipc"], hoy, comprometidos_externos
    )

    conteo_clases = {}
    for s in clasificacion.streams:
        conteo_clases[s.clase] = conteo_clases.get(s.clase, 0) + 1

    res["clasificacion.total_streams"] = str(len(clasificacion.streams))
    res["clasificacion.grupos_por_clase.COMPROMISO"] = str(
        conteo_clases.get("COMPROMISO", 0)
    )
    res["clasificacion.grupos_por_clase.HABITO"] = str(
        conteo_clases.get("HABITO", 0)
    )
    res["clasificacion.grupos_por_clase.VARIABLE"] = str(
        conteo_clases.get("VARIABLE", 0)
    )

    streams_ordenados = sorted(
        clasificacion.streams, key=lambda x: (x.clase, x.descripcion)
    )
    for s in streams_ordenados:
        safe_desc = (
            s.descripcion.lower()
            .replace(" ", "_")
            .replace("/", "_")
            .replace(":", "_")
        )
        prefix = f"clasificacion.stream.{safe_desc}"
        res[f"{prefix}.nombre"] = s.descripcion
        res[f"{prefix}.clase"] = s.clase
        res[f"{prefix}.estado"] = s.estado
        res[f"{prefix}.frecuencia"] = s.frecuencia
        res[f"{prefix}.ocurrencias"] = str(s.cantidad_ocurrencias)
        res[f"{prefix}.monto_mediano"] = _fmt_monto(s.monto_mediano_deflactado)
    return res


def medir_bloque_i(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque I: Herramienta ¿Me lo puedo permitir? para 4 escenarios."""
    res = {}
    escenarios = [
        ("S1_300k_contado", 300000.0, "contado", 1, False, None),
        ("S2_300k_6cuotas", 300000.0, "cuotas", 6, False, None),
        ("S3_1200k_12cuotas", 1200000.0, "cuotas", 12, False, None),
        ("S4_80k_contado", 80000.0, "contado", 1, False, None),
    ]
    for tag, precio, modo, cuotas, tiene_int, tna in escenarios:
        out = tools_service.calcular_puede_permitirse(
            str(usuario.id), precio, modo, cuotas, tiene_int, tna, None, db
        )
        prefix = f"puede_permitirse.{tag}"
        res[f"{prefix}.semaforo"] = _fmt_val(out.get("semaforo"))
        res[f"{prefix}.mensaje_principal"] = _fmt_val(
            out.get("mensaje_principal")
        )
        res[f"{prefix}.precio_total"] = _fmt_monto(out.get("precio_total"))
        res[f"{prefix}.saldo_disponible_actual"] = _fmt_monto(
            out.get("saldo_disponible_actual")
        )
        if modo == "contado":
            res[f"{prefix}.saldo_restante_post_compra"] = _fmt_monto(
                out.get("saldo_restante_post_compra")
            )
            res[f"{prefix}.porcentaje_del_saldo"] = _fmt_monto(
                out.get("porcentaje_del_saldo")
            )
            res[f"{prefix}.porcentaje_del_ingreso_mensual"] = _fmt_monto(
                out.get("porcentaje_del_ingreso_mensual")
            )
        else:
            res[f"{prefix}.monto_cuota"] = _fmt_monto(out.get("monto_cuota"))
            res[f"{prefix}.carga_mensual_previa"] = _fmt_monto(
                out.get("carga_mensual_previa")
            )
            res[f"{prefix}.carga_mensual_nueva_total"] = _fmt_monto(
                out.get("carga_mensual_nueva_total")
            )
            res[f"{prefix}.porcentaje_carga_sobre_ingreso"] = _fmt_monto(
                out.get("porcentaje_carga_sobre_ingreso")
            )
            res[f"{prefix}.margen_libre_post_compra"] = _fmt_monto(
                out.get("margen_libre_post_compra")
            )
    return res


def medir_bloque_j() -> dict[str, str]:
    """Bloque J: Cuotas vs Contado para S3 con inflación mensual 1.7%."""
    res = {}
    req = InstallmentConvenienceRequest(
        precio_contado=1200000.0,
        precio_total_cuotas=1200000.0,
        cantidad_cuotas=12,
        inflacion_mensual=1.7,
        tiene_interes=False,
        tna=None,
    )
    out = tools_service.calcular_conveniencia_cuotas(req)
    prefix = "cuotas_vs_contado.s3"
    res[f"{prefix}.resultado"] = _fmt_val(out.get("resultado"))
    res[f"{prefix}.precio_contado"] = _fmt_monto(out.get("precio_contado"))
    res[f"{prefix}.precio_total_cuotas_nominal"] = _fmt_monto(
        out.get("precio_total_cuotas_nominal")
    )
    res[f"{prefix}.costo_real_cuotas"] = _fmt_monto(
        out.get("costo_real_cuotas")
    )
    res[f"{prefix}.ahorro_real"] = _fmt_monto(out.get("ahorro_real"))
    res[f"{prefix}.porcentaje_ahorro"] = _fmt_monto(
        out.get("porcentaje_ahorro")
    )
    res[f"{prefix}.monto_cuota"] = _fmt_monto(out.get("monto_cuota"))
    res[f"{prefix}.inflacion_mensual_usada"] = _fmt_monto(
        out.get("inflacion_mensual_usada")
    )
    return res


def medir_bloque_k(
    db: Session, usuario: Usuario, hoy: date, ciclo: tuple[date, date]
) -> dict[str, str]:
    """Bloque K: Respuestas de texto para WhatsApp (enriquecedores determinísticos)."""
    res = {}
    intents = [
        "consultar_saldo",
        "consultar_balance",
        "consultar_proyeccion",
        "consultar_meta",
        "consultar_presupuesto",
    ]
    for intent in intents:
        res_ia = {}
        enriquecer_respuesta_por_intent(intent, res_ia, intent, usuario, db)
        res[f"whatsapp.texto.{intent}"] = res_ia.get(
            "respuesta_usuario", ""
        ).strip()

    desde_mes, hasta_mes = _rango_periodo("mes", hoy, ciclo)
    res_mes = gastos_consulta_service.calcular_gastos_periodo(
        db, usuario.id, desde_mes, hasta_mes, top_n=3
    )
    msg_mes = _formatear_respuesta_gastos(
        ETIQUETAS_PERIODO["mes"],
        None,
        False,
        float(res_mes["ars"]["total"]),
        res_mes["ars"]["cantidad"],
        float(res_mes["usd"]["total"]),
        res_mes["usd"]["cantidad"],
        res_mes["top_categorias_ars"],
    )
    res["whatsapp.texto.cuanto_gaste_este_mes"] = msg_mes.strip()

    subs = suscripcion_service.obtener_suscripciones(
        db, usuario.id, estado="activa"
    )
    totales_subs = suscripcion_service.obtener_total_mensual(db, usuario.id)
    lineas_subs = ["Tus suscripciones activas:"]
    for s in subs:
        monto_p = s.precio_actual.monto if s.precio_actual else Decimal("0")
        moneda_p = s.precio_actual.moneda if s.precio_actual else "ARS"
        mon_enum = Moneda.USD if moneda_p == "USD" else Moneda.ARS
        frec_str = (
            s.frecuencia.value
            if hasattr(s.frecuencia, "value")
            else str(s.frecuencia)
        )
        lineas_subs.append(
            f"- {s.nombre}: {formatear_monto(float(monto_p), mon_enum)} {frec_str}"
        )
    totales_str = []
    if totales_subs["total_ars"] > 0:
        totales_str.append(
            formatear_monto(float(totales_subs["total_ars"]), Moneda.ARS)
        )
    if totales_subs["total_usd"] > 0:
        totales_str.append(
            formatear_monto(float(totales_subs["total_usd"]), Moneda.USD)
        )
    lineas_subs.append(
        f"Total mensual estimado: {' y '.join(totales_str) if totales_str else '$0'}"
    )
    res["whatsapp.texto.consultar_suscripciones"] = "\n".join(
        lineas_subs
    ).strip()
    res["whatsapp.suscripciones.total_ars"] = _fmt_monto(
        totales_subs["total_ars"]
    )
    res["whatsapp.suscripciones.total_usd"] = _fmt_monto(
        totales_subs["total_usd"]
    )
    return res


def medir_bloque_l(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque L: Contexto de IA generado por _construir_contexto_financiero_uncached."""
    res = {}
    ctx = ai_service._construir_contexto_financiero_uncached(usuario, db)

    res["contexto_ia.usuario.nombre"] = _fmt_val(
        ctx.get("usuario", {}).get("nombre")
    )
    res["contexto_ia.usuario.sexo"] = _fmt_val(
        ctx.get("usuario", {}).get("sexo")
    )
    res["contexto_ia.fecha_actual"] = _fmt_val(
        ctx.get("fecha_actual", {}).get("iso")
    )
    res["contexto_ia.ciclo_actual.fecha_inicio"] = _fmt_val(
        ctx.get("ciclo_actual", {}).get("fecha_inicio")
    )
    res["contexto_ia.ciclo_actual.fecha_fin"] = _fmt_val(
        ctx.get("ciclo_actual", {}).get("fecha_fin")
    )
    res["contexto_ia.saldo_total_billeteras_pesos"] = _fmt_monto(
        ctx.get("saldo_total_billeteras_pesos")
    )
    res["contexto_ia.disponible_real_pesos"] = _fmt_monto(
        ctx.get("disponible_real_pesos")
    )
    res["contexto_ia.saldo_total_billeteras_dolares"] = _fmt_monto(
        ctx.get("saldo_total_billeteras_dolares")
    )
    res["contexto_ia.disponible_real_dolares"] = _fmt_monto(
        ctx.get("disponible_real_dolares")
    )
    res["contexto_ia.cantidad_billeteras"] = str(len(ctx.get("billeteras", [])))
    res["contexto_ia.cantidad_tarjetas"] = str(len(ctx.get("tarjetas", [])))
    res["contexto_ia.cantidad_metas_activas"] = str(
        len(ctx.get("metas_activas", []))
    )
    res["contexto_ia.cantidad_presupuestos_activos"] = str(
        len(ctx.get("presupuestos_activos", []))
    )
    res["contexto_ia.tiene_perfil_financiero"] = _fmt_val(
        "perfil_financiero" in ctx
    )
    return res


def medir_bloque_m(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque M: Suscripciones detalladas e historial de precios."""
    res = {}
    subs = suscripcion_service.obtener_suscripciones(db, usuario.id)
    res["suscripciones.total_registradas"] = str(len(subs))

    for s in subs:
        safe_name = s.nombre.lower().replace(" ", "_").replace(".", "_")
        prefix = f"suscripciones.{safe_name}"
        res[f"{prefix}.id"] = str(s.id)
        res[f"{prefix}.nombre"] = s.nombre
        res[f"{prefix}.estado"] = (
            s.estado.value if hasattr(s.estado, "value") else str(s.estado)
        )
        res[f"{prefix}.frecuencia"] = (
            s.frecuencia.value
            if hasattr(s.frecuencia, "value")
            else str(s.frecuencia)
        )
        res[f"{prefix}.proximo_cobro"] = _fmt_val(s.proximo_cobro)
        res[f"{prefix}.costo_mensual_equivalente"] = _fmt_monto(
            s.costo_mensual_equivalente
        )

        if s.precio_actual:
            res[f"{prefix}.precio_vigente.monto"] = _fmt_monto(
                s.precio_actual.monto
            )
            res[f"{prefix}.precio_vigente.moneda"] = str(s.precio_actual.moneda)
            res[f"{prefix}.precio_vigente.desde"] = _fmt_val(
                s.precio_actual.vigente_desde
            )
        else:
            res[f"{prefix}.precio_vigente.monto"] = "0.00"

        hist = s.historial_precios or []
        res[f"{prefix}.historial_precios_cantidad"] = str(len(hist))
        hist_parts = [
            f"{p.vigente_desde}:{_fmt_monto(p.monto)}{p.moneda}" for p in hist
        ]
        res[f"{prefix}.historial_precios_lista"] = "; ".join(hist_parts)
    return res


def medir_bloque_n(
    db: Session,
    usuario: Usuario,
    hoy: date,
    ini_act: date,
    fin_act: date,
    ini_sig: date,
    fin_sig: date,
) -> dict[str, str]:
    """Bloque N: Cuotas impagas (vencidas, por vencer, ciclo siguiente y deuda total)."""
    res = {}
    cuotas_impagas = (
        db.query(Cuota)
        .options(joinedload(Cuota.grupo))
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(
            GrupoCuotas.usuario_id == usuario.id,
            Cuota.pagada == False,
        )
        .all()
    )

    deuda_ars = Decimal("0")
    deuda_usd = Decimal("0")
    act_vencidas_ars = Decimal("0")
    act_vencidas_cant = 0
    act_por_vencer_ars = Decimal("0")
    act_por_vencer_cant = 0
    sig_ars = Decimal("0")
    sig_cant = 0

    for c in cuotas_impagas:
        monto = (
            c.monto_real if c.monto_real is not None else c.monto_proyectado
        ) or Decimal("0")
        moneda = c.grupo.moneda

        if moneda == Moneda.ARS:
            deuda_ars += monto
        elif moneda == Moneda.USD:
            deuda_usd += monto

        if ini_act <= c.fecha_vencimiento <= fin_act:
            if c.fecha_vencimiento < hoy:
                act_vencidas_ars += monto if moneda == Moneda.ARS else Decimal("0")
                act_vencidas_cant += 1
            else:
                act_por_vencer_ars += (
                    monto if moneda == Moneda.ARS else Decimal("0")
                )
                act_por_vencer_cant += 1
        elif ini_sig <= c.fecha_vencimiento <= fin_sig:
            sig_ars += monto if moneda == Moneda.ARS else Decimal("0")
            sig_cant += 1

    res["cuotas.deuda_total_pendiente.ars"] = _fmt_monto(deuda_ars)
    res["cuotas.deuda_total_pendiente.usd"] = _fmt_monto(deuda_usd)
    res["cuotas.deuda_total_pendiente.cantidad_total"] = str(len(cuotas_impagas))

    res["cuotas.ciclo_actual.vencidas_ars"] = _fmt_monto(act_vencidas_ars)
    res["cuotas.ciclo_actual.vencidas_cantidad"] = str(act_vencidas_cant)
    res["cuotas.ciclo_actual.por_vencer_ars"] = _fmt_monto(act_por_vencer_ars)
    res["cuotas.ciclo_actual.por_vencer_cantidad"] = str(act_por_vencer_cant)

    res["cuotas.ciclo_siguiente.impagas_ars"] = _fmt_monto(sig_ars)
    res["cuotas.ciclo_siguiente.impagas_cantidad"] = str(sig_cant)
    return res


def medir_bloque_o(db: Session, usuario: Usuario) -> dict[str, str]:
    """Bloque O: Presupuestos y metas de ahorro."""
    res = {}
    presupuestos = (
        db.query(Presupuesto)
        .options(selectinload(Presupuesto.periodos))
        .filter(Presupuesto.usuario_id == usuario.id)
        .order_by(Presupuesto.nombre.asc())
        .all()
    )
    res["presupuestos.cantidad_total"] = str(len(presupuestos))
    for p in presupuestos:
        safe_p = p.nombre.lower().replace(" ", "_")
        prefix = f"presupuestos.{safe_p}"
        res[f"{prefix}.id"] = str(p.id)
        res[f"{prefix}.nombre"] = p.nombre
        res[f"{prefix}.estado"] = _fmt_val(p.estado)
        res[f"{prefix}.limite"] = _fmt_monto(p.monto)
        res[f"{prefix}.moneda"] = _fmt_val(p.moneda)
        res[f"{prefix}.periodo"] = _fmt_val(p.periodo)
        usado = (
            float(p.monto_usado_actual)
            if getattr(p, "monto_usado_actual", None) is not None
            else 0.0
        )
        res[f"{prefix}.monto_usado"] = _fmt_monto(usado)
        res[f"{prefix}.monto_disponible"] = _fmt_monto(max(0.0, float(p.monto) - usado))

    metas = (
        db.query(Meta)
        .filter(Meta.usuario_id == usuario.id)
        .order_by(Meta.nombre.asc())
        .all()
    )
    res["metas.cantidad_total"] = str(len(metas))
    for m in metas:
        safe_m = m.nombre.lower().replace(" ", "_")
        prefix = f"metas.{safe_m}"
        res[f"{prefix}.id"] = str(m.id)
        res[f"{prefix}.nombre"] = m.nombre
        res[f"{prefix}.estado"] = _fmt_val(m.estado)
        res[f"{prefix}.objetivo"] = _fmt_monto(m.monto_objetivo)
        res[f"{prefix}.acumulado"] = _fmt_monto(m.monto_actual)
        res[f"{prefix}.moneda"] = _fmt_val(m.moneda)
        pct = (
            (float(m.monto_actual) / float(m.monto_objetivo) * 100.0)
            if m.monto_objetivo > 0
            else 0.0
        )
        res[f"{prefix}.porcentaje_completado"] = _fmt_monto(pct)
    return res


def medir_bloque_p(db: Session) -> dict[str, str]:
    """Bloque P: Registros de IPC y cotizaciones del dólar vigentes en BD."""
    res = {}
    ipcs = (
        db.execute(select(IPCCache).order_by(IPCCache.fecha_dato.desc()).limit(3))
        .scalars()
        .all()
    )
    res["ipc.registros_recientes_cantidad"] = str(len(ipcs))
    for idx, item in enumerate(ipcs, 1):
        prefix = f"ipc.registro_{idx}"
        res[f"{prefix}.fecha_dato"] = str(item.fecha_dato)
        res[f"{prefix}.indice_acumulado"] = _fmt_monto(item.indice_acumulado)
        res[f"{prefix}.fuente"] = str(item.fuente)
        res[f"{prefix}.es_estimado"] = _fmt_val(item.es_estimado)

    tipos_dolar = ["blue", "oficial", "mep", "tarjeta"]
    for t in tipos_dolar:
        cot = (
            db.execute(
                select(CotizacionDolar)
                .where(CotizacionDolar.tipo == t)
                .order_by(desc(CotizacionDolar.fecha))
            )
            .scalars()
            .first()
        )
        prefix = f"dolar.{t}"
        if cot:
            res[f"{prefix}.fecha"] = str(cot.fecha)
            res[f"{prefix}.compra"] = _fmt_monto(cot.compra)
            res[f"{prefix}.venta"] = _fmt_monto(cot.venta)
            res[f"{prefix}.promedio"] = _fmt_monto(cot.promedio)
        else:
            res[f"{prefix}.fecha"] = "null"
            res[f"{prefix}.compra"] = "0.00"
            res[f"{prefix}.venta"] = "0.00"
    return res
