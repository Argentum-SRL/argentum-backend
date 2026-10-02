"""
Servicio de perfil financiero de Argentum.

Administra el cálculo, persistencia y consulta del perfil financiero del usuario.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.perfil_financiero import PerfilFinanciero
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.models.usuario import Usuario
from decimal import Decimal
from typing import Any
from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.usuario import Moneda
from app.services.dashboard_service import get_ciclo_fechas
from app.services.datos_motor_service import cargar_datos_motor, ciclos_anteriores
from app.services.definiciones_service import ContextoDefiniciones, es_gasto, es_ingreso
from app.utils.fecha import hoy_argentina
from app.services.compromisos_service import calcular_compromisos_memoria
from app.services.ingreso_habitual_service import obtener_ingreso_habitual
from app.utils.finanzas import (
    ONE,
    ZERO,
    _indice_por_mes,
    clasificar_gastos,
    deflactar_monto,
    gasto_ciclo,
    mad,
    mediana,
    monto_mensual_deflactado_stream,
    posicion_relativa,
)

logger = logging.getLogger(__name__)


def _calcular_y_persistir_perfil_sync(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    
    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        raise ValueError(f"Usuario {usuario_id} no encontrado")

    nuevo = calcular_perfil_nuevo(db, usuario)
    perfil = db.execute(
        select(PerfilFinanciero).where(PerfilFinanciero.usuario_id == usuario_id)
    ).scalar_one_or_none()
    if perfil is None:
        perfil = PerfilFinanciero(usuario_id=usuario_id)
        db.add(perfil)
    perfil.tasa_ahorro_ars = nuevo["capacidad_ahorro"]
    perfil.tasa_ahorro_usd = None
    perfil.ratio_cuotas_ars = nuevo["gasto_comprometido_ratio"]
    perfil.ratio_cuotas_usd = None
    perfil.consistencia_registro = nuevo["cobertura_registro"]
    perfil.ultima_actualizacion = datetime.now(timezone.utc)
    db.commit()
    db.refresh(perfil)
    return perfil


def _obtener_perfil_sync(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    perfil = db.execute(
        select(PerfilFinanciero).where(PerfilFinanciero.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if perfil:
        return perfil

    perfil = _calcular_y_persistir_perfil_sync(db, usuario_id)
    return perfil


def calcular_y_persistir_perfil(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    res = _calcular_y_persistir_perfil_sync(db, usuario_id)
    if res is None:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail="Usuario no encontrado."
            )
        from uuid import uuid4
        return PerfilFinanciero(
            id=uuid4(),
            usuario_id=usuario_id,
            tasa_ahorro_ars=None,
            tasa_ahorro_usd=None,
            ratio_cuotas_ars=None,
            ratio_cuotas_usd=None,
            consistencia_registro=None,
            ultima_actualizacion=None,
            fecha_creacion=datetime.now(timezone.utc)
        )
    return res


def obtener_perfil(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    res = _obtener_perfil_sync(db, usuario_id)
    if res is None:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail="Usuario no encontrado."
            )
        from uuid import uuid4
        return PerfilFinanciero(
            id=uuid4(),
            usuario_id=usuario_id,
            tasa_ahorro_ars=None,
            tasa_ahorro_usd=None,
            ratio_cuotas_ars=None,
            ratio_cuotas_usd=None,
            consistencia_registro=None,
            ultima_actualizacion=None,
            fecha_creacion=datetime.now(timezone.utc)
        )
    return res


def generar_texto_contexto_ia(perfil: PerfilFinanciero) -> str:
    """Genera texto de perfil para el contexto IA con métricas financieras objetivas y sin juicios de conducta."""
    lineas = []
    
    # Capacidad de ahorro
    if perfil.tasa_ahorro_ars is not None:
        lineas.append(f"- Capacidad de ahorro típica ARS: {float(perfil.tasa_ahorro_ars)*100:.1f}%")
        
    # Gasto comprometido sobre ingreso
    if perfil.ratio_cuotas_ars is not None:
        lineas.append(f"- Gasto comprometido sobre ingreso ARS: {float(perfil.ratio_cuotas_ars)*100:.1f}%")

    if perfil.tasa_ahorro_ars is None and perfil.ratio_cuotas_ars is None:
        lineas.append("- Ingresos: no sabemos cuánto cobra el usuario. Debe cargar sus cobros para habilitar métricas de ahorro y compromisos sobre ingreso.")
        
    # Cobertura de registro
    if perfil.consistencia_registro is not None:
        lineas.append(f"- Cobertura de registro activo: {float(perfil.consistencia_registro)*100:.1f}%")
        
    if not lineas:
        return ""
    
    return "PERFIL FINANCIERO DEL USUARIO:\n" + "\n".join(lineas)


def guardar_snapshot_historial(
    db: Session, 
    usuario_id: UUID, 
    periodo_inicio: date, 
    periodo_fin: date
) -> HistorialPerfilFinanciero | None:
    """
    Toma el perfil actual del usuario y lo guarda como snapshot histórico.
    Solo guarda si no existe ya un snapshot para el mismo periodo_inicio.
    """
    # Verificar si ya existe snapshot para este período
    existente = db.execute(
        select(HistorialPerfilFinanciero).where(
            HistorialPerfilFinanciero.usuario_id == usuario_id,
            HistorialPerfilFinanciero.periodo_inicio == periodo_inicio
        )
    ).scalar_one_or_none()
    
    if existente:
        return existente
    
    # Obtener perfil actual
    perfil = db.execute(
        select(PerfilFinanciero).where(
            PerfilFinanciero.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not perfil:
        return None
    
    snapshot = HistorialPerfilFinanciero(
        usuario_id=usuario_id,
        periodo_inicio=periodo_inicio,
        periodo_fin=periodo_fin,
        tasa_ahorro_ars=perfil.tasa_ahorro_ars,
        tasa_ahorro_usd=perfil.tasa_ahorro_usd,
        ratio_cuotas_ars=perfil.ratio_cuotas_ars,
        ratio_cuotas_usd=perfil.ratio_cuotas_usd,
        consistencia_registro=perfil.consistencia_registro,
        fecha_snapshot=datetime.now(timezone.utc)
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def recalcular_perfil_tras_confirmacion(db: Session, usuario_id: UUID) -> None:
    """
    Trigger síncrono para recalcular el perfil cuando se confirma una transacción.
    Se llama desde el endpoint de confirmación (síncrono).
    Falla silenciosamente para no interrumpir el flujo principal.
    """
    try:
        _calcular_y_persistir_perfil_sync(db, usuario_id)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"No se pudo recalcular perfil tras confirmación para {usuario_id}: {e}")


def _suma_deflactada(txs: list[Any], ipc: Any, destino: date, moneda: Moneda, tipo: TipoTransaccion | None = None, *, ctx: ContextoDefiniciones) -> Decimal:
    ipc_map = ipc if isinstance(ipc, dict) else _indice_por_mes(ipc)
    if tipo == TipoTransaccion.EGRESO:
        return gasto_ciclo(txs, min((tx.fecha for tx in txs), default=destino), max((tx.fecha for tx in txs), default=destino), destino, ipc_map, moneda, ctx=ctx).deflactado
    total = ZERO
    for tx in txs:
        if tx.moneda != moneda:
            continue
        if tipo == TipoTransaccion.INGRESO:
            if not es_ingreso(tx, ctx):
                continue
        elif tipo == TipoTransaccion.EGRESO:
            if not es_gasto(tx, ctx):
                continue
        else:
            if not (es_gasto(tx, ctx) or es_ingreso(tx, ctx)):
                continue
        ajuste = deflactar_monto(tx.monto, tx.fecha, destino, ipc_map, moneda)
        total += ajuste.monto
    return total



def _ciclos_montos(txs: list[Any], ciclos: list[tuple[date, date]], ipc: list[Any], destino: date, moneda: Moneda, tipo: TipoTransaccion, *, ctx: ContextoDefiniciones) -> list[Decimal]:
    return [
        _suma_deflactada([tx for tx in txs if inicio <= tx.fecha <= fin], ipc, destino, moneda, tipo, ctx=ctx)
        for inicio, fin in ciclos
    ]



def _determinar_confianza_perfil(
    cant_ciclos: int,
    continuidad: Decimal,
    densidad: Decimal,
    tiene_ingresos: bool,
) -> str:
    """Evalúa la confiabilidad estadística del perfil financiero combinando historia, continuidad y densidad."""
    if cant_ciclos == 0:
        return "sin_datos"
    if cant_ciclos < 3:
        return "insuficiente"
    if not tiene_ingresos or densidad < Decimal("5") or continuidad < Decimal("0.60"):
        return "baja"
    if cant_ciclos < 6 or continuidad < Decimal("0.80") or densidad < Decimal("10"):
        return "media"
    return "alta"



def calcular_perfil_nuevo(db: Session, usuario: Usuario, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Calcula el perfil financiero objetivo basado en el marco FinHealth Score (Spend, Save, Borrow, Plan).

    Métricas calculadas sobre montos deflactados a moneda actual (IPC):
    1. Capacidad de ahorro: (Ingreso típico - Gasto típico) / Ingreso típico.
    2. Gasto comprometido sobre ingreso: Obligaciones ineludibles / Ingreso típico.
    3. Gasto en hábitos sobre ingreso: Consumos frecuentes discrecionales / Ingreso típico.
    4. Meses de cobertura (Runway): Saldo líquido en billeteras / Gasto mensual típico.
    5. Volatilidad del gasto variable: MAD(gasto variable) / Mediana(gasto variable).
    6. Ingreso típico mensual: Mediana histórica deflactada de ingresos.

    Puertas de historia:
    - < 3 ciclos: Datos insuficientes. No se emiten métricas cuantitativas para evitar falsas precisiones.
    - >= 3 ciclos: Perfil activo con estimadores no paramétricos robustos (mediana, MAD).
    - Interpretaciones relativas a la propia historia del usuario, sin etiquetas morales ni umbrales fijos.
    """
    data = data or cargar_datos_motor(db, usuario)
    hoy = data["hoy"]
    txs = data["txs"]
    ipc = data["ipc"]
    ciclos = ciclos_anteriores(usuario, hoy, 12)
    ciclos_con_datos = [c for c in ciclos if any(c[0] <= tx.fecha <= c[1] for tx in txs)]
    cant_ciclos = len(ciclos_con_datos)

    # 1. Puerta estadística por cantidad de historia
    datos_suficientes = cant_ciclos >= 3
    if not datos_suficientes:
        if cant_ciclos == 0:
            mensaje_insuficiente = (
                "Tu perfil financiero requiere al menos 3 ciclos mensuales completos para generar métricas estadísticas confiables. "
                "Hoy no contás con ciclos registrados. Registrá tus movimientos mes a mes para comenzar."
            )
        elif cant_ciclos == 1:
            mensaje_insuficiente = (
                "Tu perfil financiero requiere al menos 3 ciclos mensuales completos para generar métricas estadísticas confiables. "
                "Hoy contás con 1 de 3 ciclos necesarios (te faltan 2 ciclos con registro)."
            )
        else:
            mensaje_insuficiente = (
                "Tu perfil financiero requiere al menos 3 ciclos mensuales completos para generar métricas estadísticas confiables. "
                "Hoy contás con 2 de 3 ciclos necesarios (te falta 1 ciclo con registro)."
            )
    else:
        mensaje_insuficiente = None

    # 2. Estimación honesta de continuidad activa y densidad de registro
    primer_idx = next((i for i, c in enumerate(ciclos) if any(c[0] <= tx.fecha <= c[1] for tx in txs)), None)
    if primer_idx is not None:
        ciclos_desde_inicio = len(ciclos) - primer_idx
        continuidad = Decimal(cant_ciclos) / Decimal(max(1, ciclos_desde_inicio))
    else:
        continuidad = ZERO

    total_txs_periodo = sum(1 for tx in txs if ciclos and ciclos[0][0] <= tx.fecha <= ciclos[-1][1])
    densidad = Decimal(total_txs_periodo) / Decimal(max(1, cant_ciclos))

    comprometidos_externos = [
        *(cuota for cuota, _ in data["cuotas"] if not cuota.pagada and cuota.fecha_vencimiento >= hoy),
        *data["suscripciones"],
    ]
    ctx = data["ctx"]
    clasificacion = clasificar_gastos(txs, ciclos, ipc, hoy, comprometidos_externos, ctx=ctx)

    ingresos = _ciclos_montos(txs, ciclos, ipc, hoy, Moneda.ARS, TipoTransaccion.INGRESO, ctx=ctx)
    ingresos_con_datos = [v for v in ingresos if v > ZERO]
    tiene_ingresos = len(ingresos_con_datos) > 0

    gastos = []
    variables = []
    for inicio, fin in ciclos:
        ciclo_txs = [tx for tx in txs if inicio <= tx.fecha <= fin]
        gastos.append(_suma_deflactada([tx for tx in ciclo_txs if tx in list(clasificacion.comprometidos) or tx in list(clasificacion.habitos) or tx in list(clasificacion.variables)], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO, ctx=ctx))
        variables.append(_suma_deflactada([tx for tx in ciclo_txs if tx in list(clasificacion.variables)], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO, ctx=ctx))

    nivel_confianza = _determinar_confianza_perfil(cant_ciclos, continuidad, densidad, tiene_ingresos)

    # Advertencias metodológicas de calidad de datos
    advertencias = []
    if not tiene_ingresos and cant_ciclos >= 3:
        advertencias.append("No se registran ingresos en tus ciclos cerrados. No se pueden calcular ratios de ahorro ni de compromisos sobre ingreso.")
    if cant_ciclos >= 3 and densidad < Decimal("5"):
        advertencias.append("Densidad de registro baja (menos de 5 movimientos por mes). Los totales pueden subestimar tus gastos reales.")
    if cant_ciclos >= 3 and continuidad < Decimal("0.70"):
        advertencias.append("Existen meses sin registro intercalados en tu historial.")

    calidad_advertencia = " ".join(advertencias) if advertencias else None

    # Estimadores centrales sobre observaciones con datos
    inicio_actual, fin_actual = get_ciclo_fechas(usuario, hoy)
    actual_ingreso = _suma_deflactada([tx for tx in txs if inicio_actual <= tx.fecha <= hoy], ipc, hoy, Moneda.ARS, TipoTransaccion.INGRESO, ctx=ctx)
    actual_gasto = _suma_deflactada([tx for tx in txs if inicio_actual <= tx.fecha <= hoy], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO, ctx=ctx)

    observaciones_completas = [(ingreso, gasto) for ingreso, gasto in zip(ingresos, gastos) if ingreso > ZERO and gasto > ZERO]
    ingresos_completos = [ingreso for ingreso, _ in observaciones_completas]
    gastos_completos = [gasto for _, gasto in observaciones_completas]

    if datos_suficientes:
        res_hab = obtener_ingreso_habitual(db, usuario, hoy=hoy, ctx=ctx, txs_previa=txs, moneda=Moneda.ARS)
        ingreso_tipico = res_hab.monto
        gasto_tipico = mediana(gastos_completos or [g for g in gastos if g > ZERO])
        variable_tipico = mediana([v for v in variables if v > ZERO])
        variable_mad = mad([v for v in variables if v > ZERO])
    else:
        ingreso_tipico = None
        gasto_tipico = None
        variable_tipico = None
        variable_mad = None

    # Capacidad de ahorro (Pilar Gastar/Ahorrar)
    ahorros_historicos = [((i - g) / i) for i, g in observaciones_completas]
    if datos_suficientes and ingreso_tipico and ingreso_tipico > ZERO and gasto_tipico is not None:
        ahorro = (ingreso_tipico - gasto_tipico) / ingreso_tipico
        ahorro_min = min(ahorros_historicos) if ahorros_historicos else ahorro
        ahorro_max = max(ahorros_historicos) if ahorros_historicos else ahorro
        posicion_ahorro = posicion_relativa(ahorro, ahorros_historicos) if len(ahorros_historicos) >= 3 else None
    else:
        ahorro = None
        ahorro_min = None
        ahorro_max = None
        posicion_ahorro = None

    # Gasto comprometido (Pilar Endeudarse/Gastar) - Fase 2c
    res_comp = calcular_compromisos_memoria(
        data["cuotas"], data["suscripciones"], data["historial_subs"],
        clasificacion.streams, hoy, Moneda.ARS,
    )
    comprometido = res_comp.total
    comprometido_ratio = (comprometido / ingreso_tipico) if (ingreso_tipico and ingreso_tipico > ZERO) else None

    # Gasto en hábitos (Pilar Gastar)
    if datos_suficientes:
        habitos = sum(
            (monto_mensual_deflactado_stream(s) for s in clasificacion.streams if s.clase == "HABITO" and s.moneda == Moneda.ARS and s.estado == "MADURO"),
            ZERO,
        )
        habitos_ratio = (habitos / ingreso_tipico) if (ingreso_tipico and ingreso_tipico > ZERO) else None
    else:
        habitos = ZERO
        habitos_ratio = None

    # Runway / Cobertura líquida (Pilar Ahorrar)
    from app.models.billetera import Billetera, EstadoBilletera
    from app.models.meta import Meta
    saldo_ars = sum(
        (
            b.saldo_actual
            for b in db.execute(
                select(Billetera).where(
                    Billetera.usuario_id == usuario.id,
                    Billetera.moneda == Moneda.ARS,
                    Billetera.estado == EstadoBilletera.ACTIVA,
                    Billetera.es_inversion == False,
                )
            ).scalars()
        ),
        ZERO,
    )
    saldo_metas_ars = sum((m.monto_actual for m in db.execute(select(Meta).where(Meta.usuario_id == usuario.id, Meta.moneda == Moneda.ARS)).scalars()), ZERO)
    saldo_runway_ars = saldo_ars + saldo_metas_ars
    if datos_suficientes and gasto_tipico and gasto_tipico > ZERO:
        runway = max(ZERO, saldo_runway_ars / gasto_tipico)
    else:
        runway = None

    # Volatilidad del gasto variable (Pilar Planificar)
    if datos_suficientes and variable_tipico and variable_tipico > ZERO and variable_mad is not None:
        volatilidad = variable_mad / variable_tipico
    else:
        volatilidad = None

    posicion_ingreso = posicion_relativa(actual_ingreso, ingresos_con_datos) if (datos_suficientes and ingresos_con_datos and actual_ingreso > ZERO) else None
    posicion_gasto = posicion_relativa(actual_gasto, [v for v in gastos if v > ZERO]) if (datos_suficientes and actual_gasto > ZERO) else None

    # Interpretaciones relativas sin umbrales fijos
    interp_relativas: dict[str, str] = {}
    if ahorro is not None:
        rango_str = f"Rango histórico observado: {round(ahorro_min*100, 1)}% a {round(ahorro_max*100, 1)}% ({len(ahorros_historicos)} ciclos)" if ahorros_historicos else ""
        if posicion_ahorro is not None and len(ahorros_historicos) >= 6:
            interp_relativas["capacidad_ahorro"] = f"Percentil {round(posicion_ahorro*100)}% de tu serie histórica. {rango_str}"
        else:
            interp_relativas["capacidad_ahorro"] = f"Tasa de ahorro típica sobre ingresos deflactados. {rango_str}"

    if comprometido_ratio is not None:
        pct_comp = round(comprometido_ratio * Decimal("100"), 1)
        interp_relativas["gasto_comprometido"] = f"Demanda el {pct_comp}% de tu ingreso típico mensual (${comprometido:,.0f} / mes en cuotas, suscripciones y gastos fijos)."

    if habitos_ratio is not None:
        pct_hab = round(habitos_ratio * Decimal("100"), 1)
        interp_relativas["gasto_habitos"] = f"Representa el {pct_hab}% de tu ingreso típico mensual (${habitos:,.0f} / mes en consumos habituales elegibles)."

    if runway is not None:
        interp_relativas["runway"] = f"Tu liquidez actual (${saldo_runway_ars:,.0f}) cubre {runway:.1f} meses de tu gasto típico mensual deflactado (${gasto_tipico:,.0f}/mes)."

    if volatilidad is not None:
        pct_vol = round(volatilidad * Decimal("100"), 1)
        interp_relativas["volatilidad"] = f"Tus gastos variables fluctúan típicamente un ±{pct_vol}% respecto de tu mediana mensual (${variable_tipico:,.0f})."

    if ingreso_tipico is not None:
        interp_relativas["ingreso_tipico"] = "Ingreso habitual estimado en ARS."

    return {
        "mostrar_card": datos_suficientes,
        "datos_suficientes": datos_suficientes,
        "mensaje_insuficiente": mensaje_insuficiente,
        "calidad_registro_advertencia": calidad_advertencia,
        "ciclos_con_datos": cant_ciclos,
        "ciclos_observados": len(ciclos),
        "nivel_confianza": nivel_confianza,
        "cobertura_registro": continuidad,
        "ingreso_tipico_ars": ingreso_tipico,
        "estabilidad_ingreso_mad": (mad(ingresos_con_datos) / ingreso_tipico if (datos_suficientes and ingreso_tipico) else None),
        "ingreso_actual_percentil": posicion_ingreso,
        "gasto_comprometido_ars": comprometido,
        "gasto_comprometido_ratio": comprometido_ratio,
        "gasto_habitos_ars": habitos,
        "gasto_habitos_ratio": habitos_ratio,
        "capacidad_ahorro": ahorro,
        "capacidad_ahorro_min": ahorro_min,
        "capacidad_ahorro_max": ahorro_max,
        "capacidad_ahorro_percentil": posicion_ahorro,
        "gasto_tipico_ars": gasto_tipico,
        "saldo_disponible_ars": saldo_ars,
        "runway_meses": runway,
        "volatilidad_gasto_variable": volatilidad,
        "gasto_actual_percentil": posicion_gasto,
        "consistencia_registro": continuidad,
        "interpretaciones_relativas": interp_relativas,
        "metodo": "mediana_MAD_percentiles_IPC_finhealth",
    }

