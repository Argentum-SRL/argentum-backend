import statistics
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from uuid import UUID
from typing import Tuple, List, Dict, Any

from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from app.models.usuario import Usuario, CicloTipo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, OrigenTransaccion
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.transaccion_recurrente import TransaccionRecurrente, EstadoTransaccionRecurrente, FrecuenciaTransaccionRecurrente, TipoTransaccionRecurrente
from app.models.meta import Meta, EstadoMeta
from app.models.presupuesto import Presupuesto, EstadoPresupuesto
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.models.usuario import Moneda
from app.services.dashboard_service import get_ciclo_fechas


def construir_payload(db: Session, usuario_id: str | UUID, ciclos: int = 3) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Construye el payload financiero estructurado para enviarlo al modelo de IA,
    además de diagnosticar y retornar el perfil detectado.
    """
    # Convertir usuario_id a objeto UUID
    u_id = UUID(str(usuario_id))

    # Obtener usuario
    usuario = db.query(Usuario).filter(Usuario.id == u_id).first()
    if not usuario:
        raise ValueError(f"Usuario con id {usuario_id} no encontrado.")

    # 1. Determinar ciclos completos
    # Tomar la fecha de hoy restando 3 horas como es estándar en el dashboard
    hoy = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    fecha_inicio_curr, fecha_fin_curr = get_ciclo_fechas(usuario, hoy)

    # Determinar el inicio de la historia del usuario (mínima fecha de transacción o registro)
    primera_tx = db.query(func.min(Transaccion.fecha)).filter(
        Transaccion.usuario_id == u_id
    ).scalar()
    
    if primera_tx is None:
        primera_tx = usuario.fecha_registro.date()

    # Retroceder ciclos completos (los que terminaron antes de hoy)
    complete_cycles: List[Tuple[date, date]] = []
    ref_date = fecha_inicio_curr - timedelta(days=1)
    limit_date = primera_tx

    for _ in range(ciclos):
        inicio, fin = get_ciclo_fechas(usuario, ref_date)
        # Si el ciclo completo queda antes de que el usuario tenga historia, detenemos
        if fin < limit_date:
            break
        complete_cycles.append((inicio, fin))
        ref_date = inicio - timedelta(days=1)

    # Validaciones de cantidad de ciclos
    if len(complete_cycles) < 1:
        raise ValueError("DATOS_INSUFICIENTES: el usuario no tiene ciclos completos registrados. Se requieren al menos 2.")
    if len(complete_cycles) < 2:
        raise ValueError("DATOS_INSUFICIENTES: el usuario tiene solo 1 ciclo completo registrado. Se requieren al menos 2.")

    # Ordenar cronológicamente (más antiguo a más reciente)
    complete_cycles.reverse()
    periodo_inicio = complete_cycles[0][0]
    periodo_fin = complete_cycles[-1][1]

    # Cargar maestros para evitar queries redundantes
    categories_db = {c.id: c.nombre for c in db.query(Categoria).all()}
    subcategories_db = {s.id: s.nombre for s in db.query(Subcategoria).all()}

    # 2. Consultar transacciones confirmadas y no padre de cuotas en el período
    transactions = db.query(Transaccion).filter(
        Transaccion.usuario_id == u_id,
        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
        Transaccion.es_padre_cuotas == False,
        Transaccion.fecha >= periodo_inicio,
        Transaccion.fecha <= periodo_fin
    ).all()

    # 3. Ingresos por ciclo
    ingresos_por_ciclo: List[float] = []
    for inicio, fin in complete_cycles:
        monto_ciclo = sum(
            t.monto for t in transactions
            if t.tipo == TipoTransaccion.INGRESO and t.moneda == Moneda.ARS and inicio <= t.fecha <= fin
        )
        ingresos_por_ciclo.append(float(monto_ciclo))

    # Estabilidad y promedio de ingresos
    tiene_ingresos_any = db.query(Transaccion.id).filter(
        Transaccion.usuario_id == u_id,
        Transaccion.tipo == TipoTransaccion.INGRESO,
        Transaccion.moneda == Moneda.ARS
    ).first() is not None

    promedio_ingresos_mensual_ars = statistics.mean(ingresos_por_ciclo) if ingresos_por_ciclo else 0.0

    try:
        std_ingresos = statistics.stdev(ingresos_por_ciclo)
    except statistics.StatisticsError:
        std_ingresos = 0.0

    coef_var = std_ingresos / promedio_ingresos_mensual_ars if promedio_ingresos_mensual_ars > 0 else 1.0
    estabilidad = "estable" if coef_var < 0.15 else "variable"
    meses_fondo_recomendado = 6 if estabilidad == "estable" else 9

    # 4. Gastos agrupados por Categoría y Subcategorías
    gastos_por_categoria_dict: Dict[UUID | None, List[Transaccion]] = {}
    for t in transactions:
        if t.tipo == TipoTransaccion.EGRESO and t.moneda == Moneda.ARS:
            gastos_por_categoria_dict.setdefault(t.categoria_id, []).append(t)

    gastos_por_categoria_payload = []
    for cat_id, txs_cat in gastos_por_categoria_dict.items():
        cat_name = categories_db.get(cat_id, "Sin categoría") if cat_id else "Sin categoría"
        
        # Calcular el gasto de esta categoría en cada ciclo para promedio y variación
        gastos_por_ciclo_cat = []
        for inicio, fin in complete_cycles:
            monto_cat_ciclo = sum(t.monto for t in txs_cat if inicio <= t.fecha <= fin)
            gastos_por_ciclo_cat.append(float(monto_cat_ciclo))

        prom_cat = statistics.mean(gastos_por_ciclo_cat) if gastos_por_ciclo_cat else 0.0
        var_pct = ((gastos_por_ciclo_cat[-1] - gastos_por_ciclo_cat[0]) / gastos_por_ciclo_cat[0] * 100.0) if gastos_por_ciclo_cat and gastos_por_ciclo_cat[0] > 0 else 0.0

        # Subcategorías con >= 3 ocurrencias en el período total
        subcats_dict: Dict[UUID | None, List[Transaccion]] = {}
        for t in txs_cat:
            subcats_dict.setdefault(t.subcategoria_id, []).append(t)

        subcats_payload = []
        for subcat_id, txs_sub in subcats_dict.items():
            if subcat_id is None:
                continue
            ocurrencias = len(txs_sub)
            if ocurrencias >= 3:
                sub_name = subcategories_db.get(subcat_id, "Sin subcategoría")
                total_sub = sum(t.monto for t in txs_sub)
                prom_sub = float(total_sub) / len(complete_cycles)
                subcats_payload.append({
                    "nombre": sub_name,
                    "promedio_mensual_ars": prom_sub,
                    "ocurrencias": ocurrencias
                })

        gastos_por_categoria_payload.append({
            "categoria": cat_name,
            "promedio_mensual_ars": prom_cat,
            "variacion_pct": var_pct,
            "subcategorias": subcats_payload
        })

    # 5. Compromisos fijos
    # Cuotas activas impagas en ARS
    unpaid_cuotas = db.query(Cuota).join(GrupoCuotas).filter(
        GrupoCuotas.usuario_id == u_id,
        GrupoCuotas.estado == EstadoGrupoCuotas.ACTIVO,
        Cuota.pagada == False,
        GrupoCuotas.moneda == Moneda.ARS
    ).all()

    total_pendiente_ars = float(sum(c.monto_proyectado for c in unpaid_cuotas))
    if unpaid_cuotas:
        max_vencimiento = max(c.fecha_vencimiento for c in unpaid_cuotas)
        # Diferencia de meses entre hoy y la última cuota
        meses_hasta_liberacion = (max_vencimiento.year - hoy.year) * 12 + max_vencimiento.month - hoy.month
        meses_hasta_liberacion = max(0, meses_hasta_liberacion)
    else:
        meses_hasta_liberacion = 0

    carga_mensual_ars = total_pendiente_ars / meses_hasta_liberacion if meses_hasta_liberacion > 0 else 0.0

    # Suscripciones activas en ARS
    active_subs = db.query(Suscripcion).filter(
        Suscripcion.usuario_id == u_id,
        Suscripcion.estado == EstadoSuscripcion.ACTIVA
    ).all()

    suscripciones_list = []
    suscripciones_total_mensual_ars = 0.0
    for sub in active_subs:
        hist = db.query(HistorialSuscripcion).filter(
            HistorialSuscripcion.suscripcion_id == sub.id
        ).order_by(
            HistorialSuscripcion.vigente_desde.desc(),
            HistorialSuscripcion.fecha_creacion.desc()
        ).first()

        if hist and hist.moneda == Moneda.ARS:
            monto = float(hist.monto)
            if sub.frecuencia == FrecuenciaSuscripcion.MENSUAL:
                factor = 1.0
            elif sub.frecuencia == FrecuenciaSuscripcion.BIMESTRAL:
                factor = 0.5
            elif sub.frecuencia == FrecuenciaSuscripcion.TRIMESTRAL:
                factor = 1 / 3
            elif sub.frecuencia == FrecuenciaSuscripcion.SEMESTRAL:
                factor = 1 / 6
            elif sub.frecuencia == FrecuenciaSuscripcion.ANUAL:
                factor = 1 / 12
            else:
                factor = 1.0

            monto_mensual = monto * factor
            suscripciones_total_mensual_ars += monto_mensual
            suscripciones_list.append({
                "nombre": sub.nombre,
                "monto_mensual_ars": monto_mensual,
                "frecuencia": sub.frecuencia.value
            })

    # Recurrentes activos en ARS
    active_recurrents = db.query(TransaccionRecurrente).filter(
        TransaccionRecurrente.usuario_id == u_id,
        TransaccionRecurrente.estado == EstadoTransaccionRecurrente.ACTIVA,
        TransaccionRecurrente.tipo == TipoTransaccionRecurrente.EGRESO,
        TransaccionRecurrente.moneda == Moneda.ARS
    ).all()

    recurrentes_total_mensual_ars = 0.0
    for rec in active_recurrents:
        monto = float(rec.monto)
        if rec.frecuencia == FrecuenciaTransaccionRecurrente.MENSUAL:
            monto_mensual = monto
        elif rec.frecuencia == FrecuenciaTransaccionRecurrente.QUINCENAL:
            monto_mensual = monto * 2
        elif rec.frecuencia == FrecuenciaTransaccionRecurrente.SEMANAL:
            monto_mensual = monto * 4
        else:
            monto_mensual = monto
        recurrentes_total_mensual_ars += monto_mensual

    total_compromisos_mensual_ars = carga_mensual_ars + suscripciones_total_mensual_ars + recurrentes_total_mensual_ars
    ratio_compromisos_sobre_ingreso_pct = (total_compromisos_mensual_ars / promedio_ingresos_mensual_ars * 100.0) if promedio_ingresos_mensual_ars > 0 else 0.0

    # 6. Indicadores del Perfil
    tasa_ahorro = 0.0
    score_impulsividad = 0.0
    ratio_cuotas = 0.0
    cumplimiento_presupuesto = 0.0
    consistencia_registro = 0.0
    porcentaje_suscripciones = 0.0
    perfil_no_calculado = False

    last_profile = db.query(HistorialPerfilFinanciero).filter(
        HistorialPerfilFinanciero.usuario_id == u_id
    ).order_by(
        HistorialPerfilFinanciero.fecha_snapshot.desc()
    ).first()

    if last_profile:
        tasa_ahorro = float(last_profile.tasa_ahorro) * 100.0 if last_profile.tasa_ahorro is not None else 0.0
        score_impulsividad = float(last_profile.score_impulsividad) if last_profile.score_impulsividad is not None else 0.0
        ratio_cuotas = float(last_profile.ratio_cuotas) * 100.0 if last_profile.ratio_cuotas is not None else 0.0
        cumplimiento_presupuesto = float(last_profile.cumplimiento_presupuesto) * 100.0 if last_profile.cumplimiento_presupuesto is not None else 0.0
        consistencia_registro = float(last_profile.consistencia_registro) * 100.0 if last_profile.consistencia_registro is not None else 0.0
        porcentaje_suscripciones = float(last_profile.porcentaje_suscripciones) * 100.0 if last_profile.porcentaje_suscripciones is not None else 0.0
    else:
        perfil_no_calculado = True

    # 7. Liquidez y Billeteras
    active_wallets = db.query(Billetera).filter(
        Billetera.usuario_id == u_id,
        Billetera.estado == EstadoBilletera.ACTIVA
    ).all()

    balance_total_ars = float(sum(w.saldo_actual for w in active_wallets if w.moneda == Moneda.ARS))
    balance_usd = float(sum(w.saldo_actual for w in active_wallets if w.moneda == Moneda.USD))

    ahorro_disponible_ars = balance_total_ars - (3 * total_compromisos_mensual_ars)

    # Calcular promedio total de gastos mensuales para el fondo de emergencia
    total_gastos = sum(t.monto for t in transactions if t.tipo == TipoTransaccion.EGRESO and t.moneda == Moneda.ARS)
    promedio_gastos_mensual_ars = float(total_gastos) / len(complete_cycles)

    meses_fondo_disponible = ahorro_disponible_ars / promedio_gastos_mensual_ars if promedio_gastos_mensual_ars > 0 else 0.0
    # No permitir meses de fondo negativos
    meses_fondo_disponible = max(0.0, meses_fondo_disponible)

    # 8. Gastos Hormiga
    # Agrupar egresos por subcategoría en el rango de los ciclos completos
    subcats_hormiga_dict: Dict[UUID | None, List[Transaccion]] = {}
    for t in transactions:
        if t.tipo == TipoTransaccion.EGRESO and t.moneda == Moneda.ARS:
            subcats_hormiga_dict.setdefault(t.subcategoria_id, []).append(t)

    gastos_hormiga_payload = []
    for subcat_id, txs_sub in subcats_hormiga_dict.items():
        if subcat_id is None:
            continue
        total_ocurrencias = len(txs_sub)
        ocurrencias_promedio = total_ocurrencias / len(complete_cycles)
        total_monto_sub = sum(t.monto for t in txs_sub)
        total_mensual_sub = float(total_monto_sub) / len(complete_cycles)
        impacto_anual = total_mensual_sub * 12.0
        monto_unitario_prom = float(total_monto_sub) / total_ocurrencias

        # Filtrar monto_unitario < 5000 y >= 4 ocurrencias por ciclo
        if monto_unitario_prom < 5000.0 and ocurrencias_promedio >= 4.0:
            sub_name = subcategories_db.get(subcat_id, "Sin subcategoría")
            gastos_hormiga_payload.append({
                "subcategoria": sub_name,
                "ocurrencias_promedio_por_ciclo": ocurrencias_promedio,
                "total_mensual_ars": total_mensual_sub,
                "impacto_anual_ars": impacto_anual,
                "monto_unitario_promedio_ars": monto_unitario_prom
            })

    # Ordenar gastos hormiga por impacto_anual_ars DESC
    gastos_hormiga_payload.sort(key=lambda x: x["impacto_anual_ars"], reverse=True)

    # 9. Metas activas
    active_metas = db.query(Meta).filter(
        Meta.usuario_id == u_id,
        Meta.estado == EstadoMeta.ACTIVA
    ).all()

    metas_payload = []
    for m in active_metas:
        prog = (float(m.monto_actual) / float(m.monto_objetivo) * 100.0) if m.monto_objetivo > 0 else 0.0
        metas_payload.append({
            "nombre": m.nombre,
            "monto_objetivo": float(m.monto_objetivo),
            "monto_actual": float(m.monto_actual),
            "progreso_pct": prog
        })

    # 10. Presupuestos activos
    active_presupuestos = db.query(Presupuesto).filter(
        Presupuesto.usuario_id == u_id,
        Presupuesto.estado == EstadoPresupuesto.ACTIVO,
        Presupuesto.moneda == Moneda.ARS
    ).all()

    presupuestos_payload = []
    for p in active_presupuestos:
        # Categorías y subcategorías vinculadas al presupuesto
        p_cat_ids = [pc.categoria_id for pc in p.categorias if pc.categoria_id is not None]
        p_subcat_ids = [pc.subcategoria_id for pc in p.categorias if pc.subcategoria_id is not None]

        # Calcular uso real de este presupuesto en el ciclo actual del usuario
        uso_actual_db = db.query(func.sum(Transaccion.monto)).filter(
            Transaccion.usuario_id == u_id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.moneda == Moneda.ARS,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.es_padre_cuotas == False,
            Transaccion.fecha >= fecha_inicio_curr,
            Transaccion.fecha <= fecha_fin_curr,
            or_(
                Transaccion.categoria_id.in_(p_cat_ids) if p_cat_ids else False,
                Transaccion.subcategoria_id.in_(p_subcat_ids) if p_subcat_ids else False
            )
        ).scalar() or Decimal("0")

        limite = float(p.monto)
        uso_actual = float(uso_actual_db)
        uso_pct = (uso_actual / limite * 100.0) if limite > 0 else 0.0

        presupuestos_payload.append({
            "categoria": p.nombre,
            "limite_ars": limite,
            "uso_actual_ars": uso_actual,
            "uso_pct": uso_pct
        })

    # 11. Armar advertencias
    advertencias_datos = []
    if consistencia_registro < 60.0:
        advertencias_datos.append(f"CONSISTENCIA_BAJA: solo el {consistencia_registro:.0f}% de días tiene registros. Los resultados pueden no reflejar la realidad.")
    
    if not tiene_ingresos_any:
        advertencias_datos.append("INGRESOS_NO_REGISTRADOS: el análisis de ahorro y ratio no aplica.")

    if total_gastos > 0:
        total_sin_cat = sum(t.monto for t in transactions if t.tipo == TipoTransaccion.EGRESO and t.moneda == Moneda.ARS and t.categoria_id is None)
        ratio_sin_cat = float(total_sin_cat) / float(total_gastos)
        if ratio_sin_cat > 0.20:
            advertencias_datos.append(f"GASTOS_SIN_CATEGORIA: el {ratio_sin_cat * 100:.0f}% del gasto no tiene categoría. Precisión reducida.")

    if perfil_no_calculado:
        advertencias_datos.append("PERFIL_NO_CALCULADO: los indicadores del perfil financiero no están disponibles aún.")

    # 12. Estructurar payload final
    payload = {
        "contexto_usuario": {
            "nombre": usuario.nombre or "Usuario",
            "periodo_analizado": {
                "inicio": periodo_inicio.isoformat(),
                "fin": periodo_fin.isoformat(),
                "ciclos_completos": len(complete_cycles)
            },
            "contexto_macroeconomico": {
                "ipc_periodo_pct": 0.0,  # TODO: IPC - Integrar servicio de inflación real acumulada del período
                "economia": "argentina",
                "ciclo_facturacion": "21_al_20"
            }
        },
        "ingresos": {
            "registrado": tiene_ingresos_any,
            "promedio_mensual_ars": promedio_ingresos_mensual_ars,
            "por_ciclo": ingresos_por_ciclo,
            "estabilidad": estabilidad,
            "coeficiente_variacion": coef_var,
            "meses_fondo_recomendado": meses_fondo_recomendado
        },
        "gastos_por_categoria": gastos_por_categoria_payload,
        "compromisos_fijos": {
            "cuotas": {
                "carga_mensual_ars": carga_mensual_ars,
                "total_pendiente_ars": total_pendiente_ars,
                "meses_hasta_liberacion": meses_hasta_liberacion
            },
            "suscripciones_total_mensual_ars": suscripciones_total_mensual_ars,
            "recurrentes_total_mensual_ars": recurrentes_total_mensual_ars,
            "total_compromisos_mensual_ars": total_compromisos_mensual_ars,
            "ratio_compromisos_sobre_ingreso_pct": ratio_compromisos_sobre_ingreso_pct
        },
        "indicadores_perfil": {
            "tasa_ahorro": tasa_ahorro,
            "score_impulsividad": score_impulsividad,
            "ratio_cuotas": ratio_cuotas,
            "cumplimiento_presupuesto": cumplimiento_presupuesto,
            "consistencia_registro": consistencia_registro,
            "porcentaje_suscripciones": porcentaje_suscripciones
        },
        "liquidez": {
            "balance_total_ars": balance_total_ars,
            "balance_usd": balance_usd,
            "ahorro_disponible_ars": ahorro_disponible_ars,
            "meses_fondo_disponible": meses_fondo_disponible
        },
        "gastos_hormiga": gastos_hormiga_payload,
        "suscripciones": suscripciones_list,
        "metas": metas_payload,
        "presupuestos": presupuestos_payload,
        "advertencias_datos": advertencias_datos
    }

    # 13. Diagnosticar perfil_detectado
    if tasa_ahorro <= 0.0:
        situacion_ahorro = "sin_margen"
    elif tasa_ahorro < 10.0:
        situacion_ahorro = "margen_bajo"
    elif tasa_ahorro < 20.0:
        situacion_ahorro = "margen_moderado"
    else:
        situacion_ahorro = "margen_bueno"

    if ratio_compromisos_sobre_ingreso_pct > 50.0:
        situacion_compromisos = "critico"
    elif ratio_compromisos_sobre_ingreso_pct >= 40.0:
        situacion_compromisos = "elevado"
    elif ratio_compromisos_sobre_ingreso_pct >= 25.0:
        situacion_compromisos = "moderado"
    else:
        situacion_compromisos = "sano"

    if meses_fondo_disponible < 0.5:
        situacion_fondo_emergencias = "sin_fondo"
    elif meses_fondo_disponible < 2.0:
        situacion_fondo_emergencias = "inicio"
    elif meses_fondo_disponible < 6.0:
        situacion_fondo_emergencias = "en_progreso"
    else:
        situacion_fondo_emergencias = "objetivo_cumplido"

    perfil_detectado = {
        "tiene_ingresos_registrados": tiene_ingresos_any,
        "situacion_ahorro": situacion_ahorro,
        "situacion_compromisos": situacion_compromisos,
        "situacion_fondo_emergencias": situacion_fondo_emergencias,
        "meses_fondo_recomendados": meses_fondo_recomendado,
        "datos_confiables": consistencia_registro >= 60.0
    }

    return payload, perfil_detectado
