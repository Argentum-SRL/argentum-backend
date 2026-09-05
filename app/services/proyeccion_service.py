from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Any, Dict, List, Tuple

from sqlalchemy import and_, func, select, or_, case
from sqlalchemy.orm import Session

from app.models.usuario import Usuario, Moneda
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
from app.models.categoria import Categoria
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.cuota import Cuota
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.transaccion_recurrente import TransaccionRecurrente, EstadoTransaccionRecurrente, FrecuenciaTransaccionRecurrente, TipoTransaccionRecurrente
from app.models.tools import IPCCache
from app.services.dashboard_service import get_ciclo_fechas
from app.services.tools_service import ajustar_por_ipc
from app.utils.fecha import hoy_argentina


def _preparar_datos_proyeccion(db: Session, usuario: Usuario) -> Dict[str, Any]:
    """
    Ejecuta las consultas consolidadas para ambas monedas (ARS y USD) en una sola tanda
    para minimizar los round-trips de red hacia la base de datos.
    """
    hoy = hoy_argentina()
    fecha_inicio_actual, fecha_fin_actual = get_ciclo_fechas(usuario, hoy)

    # 1. Precargar serie completa de IPC en memoria (~100-200 filas)
    ipc_records = db.execute(select(IPCCache).order_by(IPCCache.fecha_dato.asc())).scalars().all()

    # 2. Calcular ciclos anteriores completos (hasta 6)
    ciclos_anteriores_6: List[Tuple[date, date]] = []
    fecha_referencia = fecha_inicio_actual - timedelta(days=1)
    for _ in range(6):
        inicio_ant, fin_ant = get_ciclo_fechas(usuario, fecha_referencia)
        if fin_ant < hoy:
            ciclos_anteriores_6.append((inicio_ant, fin_ant))
        fecha_referencia = inicio_ant - timedelta(days=1)

    # 3. Detección consolidada de ciclos con transacciones para ARS y USD
    ciclos_con_datos_por_moneda: Dict[Moneda, List[Tuple[date, date]]] = {
        Moneda.ARS: [],
        Moneda.USD: []
    }
    active_cycle_indices: Dict[Moneda, set[int]] = {
        Moneda.ARS: set(),
        Moneda.USD: set()
    }

    if ciclos_anteriores_6:
        cycle_detection_case = case(
            *[(and_(Transaccion.fecha >= inicio, Transaccion.fecha <= fin), idx) for idx, (inicio, fin) in enumerate(ciclos_anteriores_6)],
            else_=-1
        )
        stmt_ciclos = (
            select(Transaccion.moneda, cycle_detection_case.label("cycle_idx"))
            .where(
                and_(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.movimiento_meta_id.is_(None),
                    cycle_detection_case != -1
                )
            )
            .group_by(Transaccion.moneda, "cycle_idx")
        )
        rows_ciclos = db.execute(stmt_ciclos).all()
        for row in rows_ciclos:
            if row.cycle_idx != -1 and row.moneda in active_cycle_indices:
                active_cycle_indices[row.moneda].add(row.cycle_idx)

        for idx, (inicio, fin) in enumerate(ciclos_anteriores_6):
            if idx in active_cycle_indices[Moneda.ARS]:
                ciclos_con_datos_por_moneda[Moneda.ARS].append((inicio, fin))
            if idx in active_cycle_indices[Moneda.USD]:
                ciclos_con_datos_por_moneda[Moneda.USD].append((inicio, fin))

    # 4. Promedio histórico consolidado por categoría y ciclo (ARS + USD)
    historial_rows_por_moneda: Dict[Moneda, list] = {Moneda.ARS: [], Moneda.USD: []}
    if ciclos_anteriores_6 and any(active_cycle_indices.values()):
        cycle_history_case = case(
            *[(and_(Transaccion.fecha >= inicio, Transaccion.fecha <= fin), idx) for idx, (inicio, fin) in enumerate(ciclos_anteriores_6)],
            else_=-1
        )
        stmt_hist = (
            select(
                Transaccion.moneda,
                Transaccion.categoria_id,
                Categoria.nombre,
                cycle_history_case.label("cycle_idx"),
                func.sum(Transaccion.monto).label("total")
            )
            .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
            .where(
                and_(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.tipo == TipoTransaccion.EGRESO,
                    Transaccion.es_padre_cuotas == False,
                    Transaccion.movimiento_meta_id.is_(None),
                    or_(
                        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                        Transaccion.estado_verificacion == None
                    ),
                    cycle_history_case != -1
                )
            )
            .group_by(Transaccion.moneda, Transaccion.categoria_id, Categoria.nombre, "cycle_idx")
        )
        hist_rows = db.execute(stmt_hist).all()
        for r in hist_rows:
            if r.moneda in historial_rows_por_moneda:
                historial_rows_por_moneda[r.moneda].append(r)

    # 5. Gasto actual del ciclo por categoría (ARS + USD)
    dias_restantes_check = (fecha_fin_actual - hoy).days
    fecha_tope_actual = fecha_fin_actual if dias_restantes_check < 0 else hoy

    stmt_actual = (
        select(
            Transaccion.moneda,
            Transaccion.categoria_id,
            Categoria.nombre,
            func.sum(Transaccion.monto).label("total")
        )
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio_actual,
                Transaccion.fecha <= fecha_tope_actual,
                Transaccion.tipo == TipoTransaccion.EGRESO,
                Transaccion.es_padre_cuotas == False,
                Transaccion.movimiento_meta_id.is_(None),
                or_(
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                    Transaccion.estado_verificacion == None
                )
            )
        )
        .group_by(Transaccion.moneda, Transaccion.categoria_id, Categoria.nombre)
    )
    actual_rows_por_moneda: Dict[Moneda, list] = {Moneda.ARS: [], Moneda.USD: []}
    for r in db.execute(stmt_actual).all():
        if r.moneda in actual_rows_por_moneda:
            actual_rows_por_moneda[r.moneda].append(r)

    # 6. Cuotas comprometidas restantes (ARS + USD)
    stmt_cuotas = (
        select(
            Transaccion.moneda,
            func.sum(func.coalesce(Cuota.monto_real, Cuota.monto_proyectado)).label("total")
        )
        .join(Transaccion, Cuota.transaccion_id == Transaccion.id)
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Cuota.pagada == False,
                Cuota.fecha_vencimiento >= hoy,
                Cuota.fecha_vencimiento <= fecha_fin_actual
            )
        )
        .group_by(Transaccion.moneda)
    )
    cuotas_por_moneda: Dict[Moneda, Decimal] = {Moneda.ARS: Decimal("0"), Moneda.USD: Decimal("0")}
    for r in db.execute(stmt_cuotas).all():
        if r.moneda in cuotas_por_moneda:
            cuotas_por_moneda[r.moneda] = r.total or Decimal("0")

    # 7. Suscripciones restantes (ARS + USD)
    subquery_historial = (
        select(
            HistorialSuscripcion.suscripcion_id,
            func.max(HistorialSuscripcion.vigente_desde).label("max_vigente")
        )
        .group_by(HistorialSuscripcion.suscripcion_id)
        .subquery()
    )

    suscripciones_stmt = (
        select(
            HistorialSuscripcion.moneda,
            func.sum(HistorialSuscripcion.monto).label("total")
        )
        .join(Suscripcion, HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .join(
            subquery_historial,
            and_(
                HistorialSuscripcion.suscripcion_id == subquery_historial.c.suscripcion_id,
                HistorialSuscripcion.vigente_desde == subquery_historial.c.max_vigente
            )
        )
        .where(
            and_(
                Suscripcion.usuario_id == usuario.id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
                Suscripcion.proximo_cobro >= hoy,
                Suscripcion.proximo_cobro <= fecha_fin_actual
            )
        )
        .group_by(HistorialSuscripcion.moneda)
    )
    suscripciones_por_moneda: Dict[Moneda, Decimal] = {Moneda.ARS: Decimal("0"), Moneda.USD: Decimal("0")}
    for r in db.execute(suscripciones_stmt).all():
        if r.moneda in suscripciones_por_moneda:
            suscripciones_por_moneda[r.moneda] = r.total or Decimal("0")

    # 8. Ingresos actuales del ciclo (ARS + USD)
    stmt_ingresos = (
        select(
            Transaccion.moneda,
            func.sum(Transaccion.monto).label("total")
        )
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio_actual,
                Transaccion.fecha <= fecha_tope_actual,
                Transaccion.tipo == TipoTransaccion.INGRESO,
                Transaccion.movimiento_meta_id.is_(None),
                or_(
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                    Transaccion.estado_verificacion == None
                )
            )
        )
        .group_by(Transaccion.moneda)
    )
    ingresos_por_moneda: Dict[Moneda, Decimal] = {Moneda.ARS: Decimal("0"), Moneda.USD: Decimal("0")}
    for r in db.execute(stmt_ingresos).all():
        if r.moneda in ingresos_por_moneda:
            ingresos_por_moneda[r.moneda] = r.total or Decimal("0")

    # 9. Transacciones recurrentes de ingreso y verificación diaria
    recurrentes_activas = db.execute(
        select(TransaccionRecurrente)
        .where(
            and_(
                TransaccionRecurrente.usuario_id == usuario.id,
                TransaccionRecurrente.tipo == TipoTransaccionRecurrente.INGRESO,
                TransaccionRecurrente.estado == EstadoTransaccionRecurrente.ACTIVA
            )
        )
    ).scalars().all()

    recurrentes_por_moneda: Dict[Moneda, List[TransaccionRecurrente]] = {
        Moneda.ARS: [],
        Moneda.USD: []
    }
    for rec in recurrentes_activas:
        if rec.moneda in recurrentes_por_moneda:
            recurrentes_por_moneda[rec.moneda].append(rec)

    recurrentes_ids = [rec.id for rec in recurrentes_activas]
    recurrentes_hoy: set = set()
    if recurrentes_ids:
        stmt_hoy = select(Transaccion.recurrente_id).where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.recurrente_id.in_(recurrentes_ids),
                Transaccion.fecha == hoy
            )
        )
        recurrentes_hoy = set(db.execute(stmt_hoy).scalars().all())

    return {
        "hoy": hoy,
        "fecha_inicio_actual": fecha_inicio_actual,
        "fecha_fin_actual": fecha_fin_actual,
        "ipc_records": ipc_records,
        "ciclos_anteriores_6": ciclos_anteriores_6,
        "ciclos_con_datos_por_moneda": ciclos_con_datos_por_moneda,
        "active_cycle_indices": active_cycle_indices,
        "historial_rows_por_moneda": historial_rows_por_moneda,
        "actual_rows_por_moneda": actual_rows_por_moneda,
        "cuotas_por_moneda": cuotas_por_moneda,
        "suscripciones_por_moneda": suscripciones_por_moneda,
        "ingresos_por_moneda": ingresos_por_moneda,
        "recurrentes_por_moneda": recurrentes_por_moneda,
        "recurrentes_hoy": recurrentes_hoy,
    }


def _calcular_proyeccion_por_moneda(
    db: Session,
    usuario: Usuario,
    moneda: Moneda,
    preloaded: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    if preloaded is None:
        preloaded = _preparar_datos_proyeccion(db, usuario)

    hoy = preloaded["hoy"]
    fecha_inicio_actual = preloaded["fecha_inicio_actual"]
    fecha_fin_actual = preloaded["fecha_fin_actual"]
    ipc_records = preloaded["ipc_records"]
    ciclos_anteriores_6 = preloaded["ciclos_anteriores_6"]
    ciclos_con_datos = preloaded["ciclos_con_datos_por_moneda"].get(moneda, [])
    active_indices = preloaded["active_cycle_indices"].get(moneda, set())
    historial_rows = preloaded["historial_rows_por_moneda"].get(moneda, [])
    res_actual = preloaded["actual_rows_por_moneda"].get(moneda, [])
    cuotas_restantes = preloaded["cuotas_por_moneda"].get(moneda, Decimal("0"))
    suscripciones_restantes = preloaded["suscripciones_por_moneda"].get(moneda, Decimal("0"))
    ingresos_actuales = preloaded["ingresos_por_moneda"].get(moneda, Decimal("0"))
    recurrentes_activas = preloaded["recurrentes_por_moneda"].get(moneda, [])
    recurrentes_hoy = preloaded["recurrentes_hoy"]

    n_ciclos = len(ciclos_con_datos)
    advertencias = []

    # Paso 2: Calcular promedio histórico por categoría
    categorias_nombres = {}
    if n_ciclos > 0:
        # Mapear el índice de ciclos_anteriores_6 (0..5) a la posición dentro de ciclos_con_datos (0..n_ciclos-1)
        orig_indices_con_datos = [idx for idx in range(len(ciclos_anteriores_6)) if idx in active_indices]
        cycle_idx_map = {orig_idx: new_idx for new_idx, orig_idx in enumerate(orig_indices_con_datos)}

        totales_por_categoria_y_ciclo = [{} for _ in range(n_ciclos)]
        ipc_cache_local = {}

        for row in historial_rows:
            if row.cycle_idx in cycle_idx_map:
                new_idx = cycle_idx_map[row.cycle_idx]
                inicio, fin = ciclos_con_datos[new_idx]
                monto_final = row.total
                if moneda == Moneda.ARS:
                    midpoint_str = (inicio + (fin - inicio) // 2).strftime("%Y-%m-%d")
                    if midpoint_str not in ipc_cache_local:
                        ipc_cache_local[midpoint_str] = ajustar_por_ipc(
                            monto=1.0,
                            fecha_origen=midpoint_str,
                            db=db,
                            ipc_records=ipc_records
                        )

                    adjusted_factor = ipc_cache_local[midpoint_str]
                    if getattr(adjusted_factor, "ajuste_posible", True):
                        monto_final = Decimal(str(float(row.total) * float(adjusted_factor)))
                    else:
                        msg_warning = f"No se pudo ajustar el ciclo {inicio.strftime('%d/%m/%Y')} - {fin.strftime('%d/%m/%Y')} por falta de datos de IPC."
                        if msg_warning not in advertencias:
                            advertencias.append(msg_warning)

                totales_por_categoria_y_ciclo[new_idx][row.categoria_id] = monto_final
                categorias_nombres[row.categoria_id] = row.nombre
    else:
        totales_por_categoria_y_ciclo = []

    promedios_historicos = {}
    if n_ciclos > 0:
        todas_las_categorias = set()
        for ciclo in totales_por_categoria_y_ciclo:
            todas_las_categorias.update(ciclo.keys())

        for cat_id in todas_las_categorias:
            valores = [ciclo[cat_id] for ciclo in totales_por_categoria_y_ciclo if cat_id in ciclo]
            count_con_gasto = len(valores)
            suma_total = sum(valores)

            if count_con_gasto >= ceil(n_ciclos / 2):
                promedios_historicos[cat_id] = suma_total / Decimal(count_con_gasto)
            else:
                promedios_historicos[cat_id] = suma_total / Decimal(n_ciclos)

    # Paso 3: Calcular ritmo del ciclo actual
    dias_totales = max(1, (fecha_fin_actual - fecha_inicio_actual).days + 1)
    dias_transcurridos_calc = (hoy - fecha_inicio_actual).days + 1
    dias_transcurridos = max(1, min(dias_transcurridos_calc, dias_totales))
    dias_restantes = max(0, (fecha_fin_actual - hoy).days)

    if (fecha_fin_actual - hoy).days < 0:
        # El ciclo ya terminó, devolver datos reales
        gasto_total_real = sum(row.total for row in res_actual)

        desglose = []
        for row in res_actual:
            cat_id = row.categoria_id
            monto_real = row.total or Decimal("0")
            promedio_hist = promedios_historicos.get(cat_id, Decimal("0"))
            desglose.append({
                "categoria_id": str(cat_id) if cat_id is not None else None,
                "categoria_nombre": row.nombre or "Sin categoría",
                "gasto_actual_ciclo": float(monto_real),
                "promedio_historico": float(promedio_hist),
                "proyectado": float(monto_real),
                "fuera_de_patron": False
            })
        desglose.sort(key=lambda x: x["proyectado"], reverse=True)

        return {
            "periodo": {
                "fecha_inicio": fecha_inicio_actual.isoformat(),
                "fecha_fin": fecha_fin_actual.isoformat(),
                "dias_transcurridos": dias_totales,
                "dias_restantes": 0,
                "dias_totales": dias_totales
            },
            "gasto_proyectado_total": float(gasto_total_real),
            "balance_proyectado": float(ingresos_actuales - gasto_total_real),
            "ingresos_proyectados": float(ingresos_actuales),
            "certezas": {"cuotas_restantes": 0, "suscripciones_restantes": 0, "total": 0},
            "desglose_por_categoria": desglose,
            "nivel_confianza": "alto",
            "ciclos_analizados": n_ciclos,
            "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
            "advertencias": ["El ciclo actual ya finalizó."],
            "datos_suficientes": True
        }

    # Gasto actual por categoría
    gasto_actual_por_categoria = {row.categoria_id: row.total for row in res_actual}
    for row in res_actual:
        categorias_nombres[row.categoria_id] = row.nombre

    # Paso 4: Calcular pesos
    if n_ciclos == 0:
        peso_historial = 0.0
        peso_actual = 1.0
    elif n_ciclos == 1:
        peso_historial = 0.6
        peso_actual = 0.4
    elif n_ciclos <= 3:
        peso_historial = 0.7
        peso_actual = 0.3
    else:
        peso_historial = 0.75
        peso_actual = 0.25

    if dias_transcurridos < 5:
        peso_historial = 1.0
        peso_actual = 0.0

    # Paso 5: Proyectar por categoría
    desglose = []
    gasto_proyectado_categorias = Decimal("0")

    todas_cat_ids = set(promedios_historicos.keys()) | set(gasto_actual_por_categoria.keys())

    for cat_id in todas_cat_ids:
        promedio_hist = promedios_historicos.get(cat_id, Decimal("0"))
        actual = gasto_actual_por_categoria.get(cat_id, Decimal("0"))

        ritmo_actual = (actual / Decimal(dias_transcurridos)) * Decimal(dias_totales)
        proyectado = (promedio_hist * Decimal(peso_historial)) + (ritmo_actual * Decimal(peso_actual))

        # Detección de fuera de patrón
        fuera_de_patron = False
        if dias_transcurridos >= 5 and promedio_hist > 0:
            ritmo_proyectado_al_paso_actual = promedio_hist * (Decimal(dias_transcurridos) / Decimal(dias_totales))
            if actual > ritmo_proyectado_al_paso_actual * Decimal("1.4"):
                fuera_de_patron = True

        gasto_proyectado_categorias += proyectado

        desglose.append({
            "categoria_id": str(cat_id) if cat_id is not None else None,
            "categoria_nombre": categorias_nombres.get(cat_id) or "Sin categoría",
            "gasto_actual_ciclo": float(actual),
            "promedio_historico": float(promedio_hist),
            "proyectado": float(proyectado),
            "fuera_de_patron": fuera_de_patron
        })

    desglose.sort(key=lambda x: x["proyectado"], reverse=True)

    # Paso 6: Sumar certezas
    total_certezas = cuotas_restantes + suscripciones_restantes
    gasto_proyectado_total = gasto_proyectado_categorias + total_certezas

    # Paso 7: Ingresos proyectados
    ingresos_recurrentes_pendientes = Decimal("0")
    for rec in recurrentes_activas:
        ya_genero_hoy = rec.id in recurrentes_hoy
        start_date = hoy if not ya_genero_hoy else hoy + timedelta(days=1)

        if start_date > fecha_fin_actual:
            continue

        if rec.frecuencia == FrecuenciaTransaccionRecurrente.MENSUAL:
            if rec.dia_registro >= start_date.day and rec.dia_registro <= fecha_fin_actual.day:
                ingresos_recurrentes_pendientes += rec.monto

        elif rec.frecuencia == FrecuenciaTransaccionRecurrente.SEMANAL:
            current = start_date
            while current <= fecha_fin_actual:
                if current.weekday() == rec.dia_registro:
                    ingresos_recurrentes_pendientes += rec.monto
                current += timedelta(days=1)

        elif rec.frecuencia == FrecuenciaTransaccionRecurrente.QUINCENAL:
            current = start_date
            while current <= fecha_fin_actual:
                if current.day == rec.dia_registro or current.day == ((rec.dia_registro + 15) % 30 or 30):
                    ingresos_recurrentes_pendientes += rec.monto
                current += timedelta(days=1)

    ingresos_proyectados = ingresos_actuales + ingresos_recurrentes_pendientes

    # Paso 8: Nivel de confianza y Gate de datos insuficientes
    datos_suficientes = True
    if n_ciclos == 0 and dias_transcurridos < 5:
        datos_suficientes = False
        nivel_confianza = "bajo"
        msg_insuficiente = "sin historial suficiente para proyectar con confianza"
        if msg_insuficiente not in advertencias:
            advertencias.append(msg_insuficiente)
    else:
        if n_ciclos >= 4 and dias_transcurridos >= 5:
            nivel_confianza = "alto"
        elif n_ciclos == 0:
            nivel_confianza = "bajo"
        else:
            nivel_confianza = "medio"

    # Paso 9: Advertencias adicionales
    if n_ciclos == 0 and datos_suficientes:
        advertencias.append("Proyección basada solo en este ciclo. Mejorará con el tiempo.")
    elif 1 <= n_ciclos <= 2:
        advertencias.append("Todavía tenemos poco historial tuyo. La proyección va a mejorar.")

    if dias_transcurridos < 5 and datos_suficientes:
        advertencias.append("Recién empieza el ciclo. La proyección se basa principalmente en tus ciclos anteriores.")

    categorias_fuera = [d["categoria_nombre"] for d in desglose if d["fuera_de_patron"]]
    if categorias_fuera:
        if len(categorias_fuera) > 1:
            cats_str = ", ".join(categorias_fuera[:-1]) + " y " + categorias_fuera[-1]
        else:
            cats_str = categorias_fuera[0]
        advertencias.append(f"Este ciclo estás gastando más de lo habitual en {cats_str}.")

    return {
        "periodo": {
            "fecha_inicio": fecha_inicio_actual.isoformat(),
            "fecha_fin": fecha_fin_actual.isoformat(),
            "dias_transcurridos": dias_transcurridos,
            "dias_restantes": dias_restantes,
            "dias_totales": dias_totales
        },
        "gasto_proyectado_total": float(gasto_proyectado_total),
        "balance_proyectado": float(ingresos_proyectados - gasto_proyectado_total),
        "ingresos_proyectados": float(ingresos_proyectados),
        "certezas": {
            "cuotas_restantes": float(cuotas_restantes),
            "suscripciones_restantes": float(suscripciones_restantes),
            "total": float(total_certezas)
        },
        "desglose_por_categoria": desglose,
        "nivel_confianza": nivel_confianza,
        "ciclos_analizados": n_ciclos,
        "pesos": {
            "historial": float(peso_historial),
            "ciclo_actual": float(peso_actual)
        },
        "advertencias": advertencias,
        "datos_suficientes": datos_suficientes
    }


def calcular_proyeccion(db: Session, usuario: Usuario) -> Dict[str, Any]:
    preloaded = _preparar_datos_proyeccion(db, usuario)
    return {
        "ars": _calcular_proyeccion_por_moneda(db, usuario, Moneda.ARS, preloaded=preloaded),
        "usd": _calcular_proyeccion_por_moneda(db, usuario, Moneda.USD, preloaded=preloaded)
    }
