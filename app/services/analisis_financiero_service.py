from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.suscripcion import EstadoSuscripcion, Suscripcion
from app.models.tools import IPCCache
from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion, Transaccion
from app.models.transaccion_recurrente import EstadoTransaccionRecurrente, TipoTransaccionRecurrente, TransaccionRecurrente
from app.models.usuario import Moneda, Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina
from app.utils.finanzas import (
    ClasificacionGasto, StreamRecurrente, ZERO, ONE, clasificar_gastos,
    deflactar_monto, es_gasto_consumo, gasto_ciclo, mad, mediana, percentil,
    posicion_relativa, monto_mensual_deflactado_stream, pinball_loss,
    estimar_gasto_diario_basico_robusto, student_t_critical, weighted_quantile,
    evaluar_puerta_calibracion, MINIMO_CICLOS_EVALUABLES_CALIBRACION,
    UMBRAL_COBERTURA_MINIMA_80, UMBRAL_ANCHO_MAXIMO_RELATIVO_80, NIVEL_EVALUADO_PUERTA
)


CONFIDENCE_BY_CYCLES = {0: "sin_datos", 1: "inicial", 2: "baja", 3: "media"}


def _ciclos(usuario: Usuario, fecha_inicio: date, hasta: date, maximo: int | None = None) -> list[tuple[date, date]]:
    result: list[tuple[date, date]] = []
    cursor = fecha_inicio
    while cursor <= hasta and (maximo is None or len(result) < maximo):
        inicio, fin = get_ciclo_fechas(usuario, cursor)
        result.append((inicio, min(fin, hasta)))
        cursor = fin + timedelta(days=1)
    return result


def _ciclos_anteriores(usuario: Usuario, hoy: date, cantidad: int = 12) -> list[tuple[date, date]]:
    inicio_actual, _ = get_ciclo_fechas(usuario, hoy)
    result = []
    cursor = inicio_actual - timedelta(days=1)
    for _ in range(cantidad):
        inicio, fin = get_ciclo_fechas(usuario, cursor)
        result.append((inicio, fin))
        cursor = inicio - timedelta(days=1)
    return list(reversed(result))


def _carga(db: Session, usuario: Usuario, fecha_referencia: date | None = None) -> dict[str, Any]:
    hoy = fecha_referencia or hoy_argentina()
    txs = db.execute(
        select(Transaccion)
        .options(
            joinedload(Transaccion.categoria),
            joinedload(Transaccion.subcategoria),
            joinedload(Transaccion.billetera),
        )
        .where(Transaccion.usuario_id == usuario.id, Transaccion.fecha <= hoy)
        .order_by(Transaccion.fecha)
    ).scalars().all()
    ipc = db.execute(select(IPCCache).order_by(IPCCache.fecha_dato)).scalars().all()
    cuotas = db.execute(
        select(Cuota, GrupoCuotas)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(GrupoCuotas.usuario_id == usuario.id)
    ).all()
    suscripciones = db.execute(
        select(Suscripcion).where(Suscripcion.usuario_id == usuario.id, Suscripcion.estado == EstadoSuscripcion.ACTIVA)
    ).scalars().all()
    historial_subs = db.execute(
        select(HistorialSuscripcion).join(Suscripcion).where(Suscripcion.usuario_id == usuario.id)
    ).scalars().all()
    recurrentes = db.execute(
        select(TransaccionRecurrente).where(
            TransaccionRecurrente.usuario_id == usuario.id,
            TransaccionRecurrente.estado == EstadoTransaccionRecurrente.ACTIVA,
        )
    ).scalars().all()
    return {"hoy": hoy, "txs": txs, "ipc": ipc, "cuotas": cuotas, "suscripciones": suscripciones, "historial_subs": historial_subs, "recurrentes": recurrentes}


def _tx_valido(tx: Any) -> bool:
    return tx.estado_verificacion in (None, EstadoVerificacionTransaccion.CONFIRMADA) and not tx.es_padre_cuotas


def _suma_deflactada(txs: list[Any], ipc: list[Any], destino: date, moneda: Moneda, tipo: TipoTransaccion | None = None) -> Decimal:
    if tipo == TipoTransaccion.EGRESO:
        return gasto_ciclo(txs, min((tx.fecha for tx in txs), default=destino), max((tx.fecha for tx in txs), default=destino), destino, ipc, moneda).deflactado
    total = ZERO
    for tx in txs:
        if tx.moneda != moneda or not _tx_valido(tx) or (tipo is not None and tx.tipo != tipo):
            continue
        if tipo == TipoTransaccion.EGRESO and not es_gasto_consumo(tx):
            continue
        ajuste = deflactar_monto(tx.monto, tx.fecha, destino, ipc, moneda)
        total += ajuste.monto
    return total


def _ciclos_montos(txs: list[Any], ciclos: list[tuple[date, date]], ipc: list[Any], destino: date, moneda: Moneda, tipo: TipoTransaccion) -> list[Decimal]:
    return [
        _suma_deflactada([tx for tx in txs if inicio <= tx.fecha <= fin], ipc, destino, moneda, tipo)
        for inicio, fin in ciclos
    ]


def _confidence(ciclos: int, cobertura: Decimal) -> str:
    if ciclos == 0:
        return "sin_datos"
    if ciclos == 1:
        return "inicial"
    if ciclos == 2:
        return "baja"
    if ciclos == 3 or cobertura < Decimal("0.75"):
        return "media"
    if ciclos >= 6 and cobertura >= Decimal("0.80"):
        return "alto"
    return "media"


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
    data = data or _carga(db, usuario)
    hoy = data["hoy"]
    txs = data["txs"]
    ipc = data["ipc"]
    ciclos = _ciclos_anteriores(usuario, hoy, 12)
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
    clasificacion = clasificar_gastos(txs, ciclos, ipc, hoy, comprometidos_externos)

    ingresos = _ciclos_montos(txs, ciclos, ipc, hoy, Moneda.ARS, TipoTransaccion.INGRESO)
    ingresos_con_datos = [v for v in ingresos if v > ZERO]
    tiene_ingresos = len(ingresos_con_datos) > 0

    gastos = []
    variables = []
    for inicio, fin in ciclos:
        ciclo_txs = [tx for tx in txs if inicio <= tx.fecha <= fin]
        gastos.append(_suma_deflactada([tx for tx in ciclo_txs if tx in list(clasificacion.comprometidos) or tx in list(clasificacion.habitos) or tx in list(clasificacion.variables)], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO))
        variables.append(_suma_deflactada([tx for tx in ciclo_txs if tx in list(clasificacion.variables)], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO))

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
    actual_ingreso = _suma_deflactada([tx for tx in txs if inicio_actual <= tx.fecha <= hoy], ipc, hoy, Moneda.ARS, TipoTransaccion.INGRESO)
    actual_gasto = _suma_deflactada([tx for tx in txs if inicio_actual <= tx.fecha <= hoy], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO)

    observaciones_completas = [(ingreso, gasto) for ingreso, gasto in zip(ingresos, gastos) if ingreso > ZERO and gasto > ZERO]
    ingresos_completos = [ingreso for ingreso, _ in observaciones_completas]
    gastos_completos = [gasto for _, gasto in observaciones_completas]

    if datos_suficientes:
        ingreso_tipico = mediana(ingresos_completos or ingresos_con_datos)
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

    # Gasto comprometido (Pilar Endeudarse/Gastar)
    cuotas = data["cuotas"]
    comprometido = sum((c.monto_real or c.monto_proyectado or ZERO for c, grupo in cuotas if grupo.moneda == Moneda.ARS and not c.pagada and c.fecha_vencimiento >= hoy), ZERO)
    comprometido += sum((h.monto for h in data["historial_subs"] if h.moneda == Moneda.ARS and any(s.id == h.suscripcion_id for s in data["suscripciones"])), ZERO)
    if datos_suficientes:
        comprometido += sum(
            (monto_mensual_deflactado_stream(s) for s in clasificacion.streams if s.clase == "COMPROMISO" and s.senal != "DECLARADO" and s.moneda == Moneda.ARS),
            ZERO,
        )
    comprometido_ratio = (comprometido / ingreso_tipico) if (datos_suficientes and ingreso_tipico and ingreso_tipico > ZERO) else None

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
    from app.models.billetera import Billetera
    saldo_ars = sum((b.saldo_actual for b in db.execute(select(Billetera).where(Billetera.usuario_id == usuario.id, Billetera.moneda == Moneda.ARS)).scalars()), ZERO)
    if datos_suficientes and gasto_tipico and gasto_tipico > ZERO:
        runway = max(ZERO, saldo_ars / gasto_tipico)
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
        interp_relativas["gasto_comprometido"] = f"Demanda el {pct_comp}% de tu ingreso típico mensual (${comprometido:,.0f} / mes en compromisos fijos)."

    if habitos_ratio is not None:
        pct_hab = round(habitos_ratio * Decimal("100"), 1)
        interp_relativas["gasto_habitos"] = f"Representa el {pct_hab}% de tu ingreso típico mensual (${habitos:,.0f} / mes en consumos habituales elegibles)."

    if runway is not None:
        interp_relativas["runway"] = f"Tu liquidez actual (${saldo_ars:,.0f}) cubre {runway:.1f} meses de tu gasto típico mensual deflactado (${gasto_tipico:,.0f}/mes)."

    if volatilidad is not None:
        pct_vol = round(volatilidad * Decimal("100"), 1)
        interp_relativas["volatilidad"] = f"Tus gastos variables fluctúan típicamente un ±{pct_vol}% respecto de tu mediana mensual (${variable_tipico:,.0f})."

    if ingreso_tipico is not None:
        if posicion_ingreso is not None:
            interp_relativas["ingreso_tipico"] = f"Mediana histórica deflactada. Ciclo actual en percentil {round(posicion_ingreso*100)}% de tus ciclos con ingreso."
        else:
            interp_relativas["ingreso_tipico"] = "Mediana histórica deflactada de tus ciclos con ingreso."

    return {
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


def _historial_categorias(txs: list[Any], ciclos: list[tuple[date, date]], ipc: list[Any], destino: date, moneda: Moneda, clasificacion: ClasificacionGasto) -> dict[Any, list[Decimal]]:
    resultado: dict[Any, list[Decimal]] = {}
    variables = set(clasificacion.variables) | set(clasificacion.recurrentes_detectados)
    for inicio, fin in ciclos:
        por_cat: dict[Any, Decimal] = {}
        for tx in txs:
            if tx not in variables or tx.moneda != moneda or not (inicio <= tx.fecha <= fin):
                continue
            valor = deflactar_monto(tx.monto, tx.fecha, destino, ipc, moneda).monto
            por_cat[tx.categoria_id] = por_cat.get(tx.categoria_id, ZERO) + valor
        for cat, valor in por_cat.items():
            resultado.setdefault(cat, []).append(valor)
    return resultado


def _cantidad_hasta(cuotas: list[tuple[Any, Any]], hoy: date, fin: date, moneda: Moneda) -> Decimal:
    return sum((c.monto_real or c.monto_proyectado or ZERO for c, grupo in cuotas if grupo.moneda == moneda and not c.pagada and hoy <= c.fecha_vencimiento <= fin), ZERO)


def evaluar_escalera_historia(cant_ciclos: int) -> tuple[bool, str, str | None]:
    """Define qué devuelve la proyección según la cantidad de ciclos con datos.

    Cortes estadísticos fundamentados:
    - 0 ciclos: Sin observaciones (N=0). Varianza previa infinita. Se prohíbe rango inventado.
      Muestra solo lo CIERTO (cuotas y suscripciones).
    - 1 ciclo: N=1. Grados de libertad N-1=0. Dispersión indefinida. Imposible calibrar intervalo.
    - 2 ciclos: N=2. Rango muestral cubre en promedio solo 33.3% ((N-1)/(N+1)). Para cubrir 80% o 95%
      exige multiplicadores extremos de Student-t (t=12.71), generando rangos no informativos.
    - 3 a 5 ciclos: N>=3. Umbral mínimo no paramétrico. Cobertura del rango muestral alcanza 50%.
      Permite estimar mediana robusta, MAD y calibrar intervalo al 50% y 80% con corrección de muestra finita.
    - 6 a 11 ciclos: N>=6. Muestra semestral. Cobertura de rango muestral sube a 71.4%.
      Mediana y MAD altamente estables (breakdown point 50%).
    - 12 a 23 ciclos: N>=12. Ciclo anual completo. Cobertura muestral 84.6%. Absorbe variaciones estacionales mes a mes.
    - 24+ ciclos: N>=24. Muestra bianual. Permite predicción conforme plena al 95% (N>=19) y contraste estacional.
    """
    if cant_ciclos == 0:
        return (
            False,
            "sin_datos",
            "No contás con ciclos históricos registrados. Mostramos tus compromisos ciertos (cuotas y suscripciones). Necesitás al menos 3 ciclos para una proyección probabilística calibrada.",
        )
    elif cant_ciclos == 1:
        return (
            False,
            "inicial",
            "Tenés 1 ciclo registrado (te faltan 2). Mostramos tus compromisos ciertos del ciclo. La proyección probabilística se activará al alcanzar 3 ciclos.",
        )
    elif cant_ciclos == 2:
        return (
            False,
            "baja",
            "Tenés 2 ciclos registrados (te falta 1). Mostramos tus compromisos ciertos del ciclo. La proyección probabilística se activará con tu próximo ciclo cerrado.",
        )
    elif cant_ciclos < 6:
        return (True, "baja", None)
    elif cant_ciclos < 12:
        return (True, "media", None)
    else:
        return (True, "alta", None)


def detectar_y_ajustar_estacionalidad(
    ciclos_historicos: list[tuple[date, date, Decimal]],
    mes_objetivo: int,
) -> tuple[bool, Decimal, str]:
    """Evalúa estacionalidad anual para el mes objetivo (ej. aguinaldo junio/diciembre, vacaciones, colegio).

    Requiere al menos 24 ciclos históricos completos y al menos 2 observaciones del mismo mes calendario.
    Si nadie llega, se mantiene implementada y apagada documentando la causa estadística.
    """
    if len(ciclos_historicos) < 24:
        return (
            False,
            ONE,
            "Historia insuficiente (<24 ciclos) para contrastar estacionalidad anual con significancia estadística.",
        )
    mismo_mes = [monto for _, fin, monto in ciclos_historicos if fin.month == mes_objetivo and monto > ZERO]
    if len(mismo_mes) < 2:
        return (
            False,
            ONE,
            f"Menos de 2 observaciones del mes {mes_objetivo} disponibles para validar patrón estacional recurrente.",
        )
    med_general = mediana([monto for _, _, monto in ciclos_historicos if monto > ZERO]) or ONE
    med_mes = mediana(mismo_mes) or med_general
    factor = (med_mes / med_general).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return (True, factor, f"Estacionalidad mes {mes_objetivo} detectada con {len(mismo_mes)} ciclos observados.")


def evaluar_calibracion_usuario(
    data: dict[str, Any],
    usuario: Usuario,
    moneda: Moneda = Moneda.ARS,
    compromiso_tx_ids: set[Any] | None = None,
) -> dict[str, Any]:
    """Ejecuta una prueba de calibración individual en backtest sobre los ciclos cerrados del usuario.

    Reutiliza estrictamente los datos ya cargados en memoria en `data` para no realizar
    consultas adicionales a la base de datos (0 extra queries).
    Evalúa si la cobertura empírica al 80% y el ancho del intervalo son informativos.
    """
    hoy = data["hoy"]
    anteriores = _ciclos_anteriores(usuario, hoy, 12)
    cerrados = [c for c in anteriores if c[1] < hoy]

    compr_ids = set(compromiso_tx_ids or set())
    resultados_ciclos: list[dict[str, Any]] = []

    for inicio_k, fin_k in cerrados:
        fecha_corte_k = inicio_k - timedelta(days=1)
        txs_previas = [tx for tx in data["txs"] if tx.fecha <= fecha_corte_k and tx.moneda == moneda]

        # Consumo real del ciclo k
        txs_k = [tx for tx in data["txs"] if inicio_k <= tx.fecha <= fin_k and tx.moneda == moneda and es_gasto_consumo(tx)]
        gc = gasto_ciclo(txs_k, inicio_k, fin_k, fin_k, data["ipc"], moneda)
        y_real = gc.deflactado
        if y_real <= ZERO:
            continue

        anteriores_k = _ciclos_anteriores(usuario, inicio_k, 12)
        ciclos_con_datos = [
            (c_ini, c_fin) for c_ini, c_fin in anteriores_k
            if any(c_ini <= tx.fecha <= c_fin for tx in txs_previas)
        ]

        if len(ciclos_con_datos) < 3:
            continue

        comprometidos_ext = [
            *(c for c, _ in data["cuotas"] if not c.pagada and c.fecha_vencimiento >= inicio_k),
            *data["suscripciones"]
        ]
        clasif_k = clasificar_gastos(txs_previas, anteriores_k, data["ipc"], inicio_k, comprometidos_ext)
        compr_ids_k = {tx_id for s in clasif_k.streams if s.clase == "COMPROMISO" for tx_id in s.transacciones_ids}
        compr_ids_k.update(
            tx.id for tx in txs_previas
            if getattr(tx, "es_recurrente", False) or getattr(tx, "recurrente_id", None) is not None
        )

        # Compromisos ciertos pendientes al inicio del ciclo k
        cuotas_p = sum(
            (c.monto_real or c.monto_proyectado or ZERO
             for c, g in data["cuotas"]
             if g.moneda == moneda and not c.pagada and inicio_k <= c.fecha_vencimiento <= fin_k),
            ZERO,
        )
        subs_p = sum(
            (h.monto for h in data["historial_subs"]
             if h.moneda == moneda and any(s.id == h.suscripcion_id and inicio_k <= s.proximo_cobro <= fin_k for s in data["suscripciones"])),
            ZERO,
        )
        compr_mad_p = ZERO
        for s in clasif_k.streams:
            if s.clase == "COMPROMISO" and s.estado == "MADURO" and s.moneda == moneda and s.senal != "DECLARADO":
                if s.proxima_fecha_esperada and inicio_k <= s.proxima_fecha_esperada <= fin_k:
                    compr_mad_p += s.monto_mediano_deflactado
        base_compromisos = cuotas_p + subs_p + compr_mad_p

        vars_hist: list[Decimal] = []
        for cp_ini, cp_fin in ciclos_con_datos:
            txs_c = [tx for tx in txs_previas if cp_ini <= tx.fecha <= cp_fin and es_gasto_consumo(tx)]
            txs_v = [tx for tx in txs_c if tx.id not in compr_ids_k]
            dias_c = Decimal((cp_fin - cp_ini).days + 1)
            t_base, tot_base, tot_shock = estimar_gasto_diario_basico_robusto(txs_v, dias_c, fin_k, data["ipc"], moneda)
            vars_hist.append(tot_base + tot_shock)

        ciclos_con_var = [v for v in vars_hist if v > ZERO]
        if len(ciclos_con_var) < 3:
            continue

        K = len(vars_hist)
        decay = Decimal("0.70")
        weights = [decay ** Decimal(K - 1 - i) for i in range(K)]
        med_var = weighted_quantile(vars_hist, weights, Decimal("0.50"))
        p25_var = weighted_quantile(vars_hist, weights, Decimal("0.25"))
        p75_var = weighted_quantile(vars_hist, weights, Decimal("0.75"))
        p10_var = weighted_quantile(vars_hist, weights, Decimal("0.10"))
        p90_var = weighted_quantile(vars_hist, weights, Decimal("0.90"))

        mad_var = mad(vars_hist) or ZERO
        sigma_robusta = mad_var * Decimal("1.4826")
        iqr_disp = (p75_var - p25_var) / Decimal("1.349") if p75_var > p25_var else ZERO
        p90_disp = (p90_var - p10_var) / Decimal("2.563") if p90_var > p10_var else ZERO
        dispersion = max(sigma_robusta, iqr_disp, p90_disp)

        if dispersion <= ZERO or (p90_var - p10_var) <= ZERO:
            continue

        df = max(1, K - 1)
        t_50 = student_t_critical(df, "50")
        t_80 = student_t_critical(df, "80")
        t_95 = student_t_critical(df, "95")
        f_m = Decimal(str((1 + 1 / max(1, K)) ** 0.5))

        q50 = max(ZERO, base_compromisos + med_var)
        q25 = max(ZERO, min(q50, base_compromisos + min(p25_var, med_var - t_50 * dispersion * f_m)))
        q75 = max(q50, base_compromisos + max(p75_var, med_var + t_50 * dispersion * f_m))
        q10 = max(ZERO, min(q25, base_compromisos + min(p10_var, med_var - t_80 * dispersion * f_m)))
        q90 = max(q75, base_compromisos + max(p90_var, med_var + t_80 * dispersion * f_m))
        max_obs = base_compromisos + max(vars_hist)
        q975 = max(q90, max_obs, base_compromisos + med_var + t_95 * dispersion * f_m)
        q025 = max(ZERO, min(q10, base_compromisos + med_var - t_95 * dispersion * f_m))

        cub_50 = (q25 <= y_real <= q75)
        cub_80 = (q10 <= y_real <= q90)
        cub_95 = (q025 <= y_real <= q975)
        ancho_80 = q90 - q10
        ancho_80_rel = (ancho_80 / y_real) if y_real > ZERO else ZERO

        resultados_ciclos.append({
            "ciclo": fin_k.strftime("%Y-%m"),
            "real": y_real,
            "q50": q50,
            "q10": q10,
            "q90": q90,
            "cubierto_50": cub_50,
            "cubierto_80": cub_80,
            "cubierto_95": cub_95,
            "ancho_80": ancho_80,
            "ancho_80_rel": ancho_80_rel,
        })

    n_eval = len(resultados_ciclos)
    if n_eval == 0:
        pasa, motivo, msg = evaluar_puerta_calibracion(0, None, None)
        return {
            "pasa_puerta": False,
            "ciclos_evaluados": 0,
            "cobertura_50": None,
            "cobertura_80": None,
            "cobertura_95": None,
            "ancho_medio_80_rel": None,
            "motivo": motivo,
            "mensaje": msg,
            "detalles": [],
        }

    cob_50 = Decimal(sum(1 for r in resultados_ciclos if r["cubierto_50"])) / Decimal(n_eval)
    cob_80 = Decimal(sum(1 for r in resultados_ciclos if r["cubierto_80"])) / Decimal(n_eval)
    cob_95 = Decimal(sum(1 for r in resultados_ciclos if r["cubierto_95"])) / Decimal(n_eval)
    ancho_rel = sum((r["ancho_80_rel"] for r in resultados_ciclos), ZERO) / Decimal(n_eval)

    pasa, motivo, msg = evaluar_puerta_calibracion(n_eval, cob_80, ancho_rel)
    return {
        "pasa_puerta": pasa,
        "ciclos_evaluados": n_eval,
        "cobertura_50": float(cob_50),
        "cobertura_80": float(cob_80),
        "cobertura_95": float(cob_95),
        "ancho_medio_80_rel": float(ancho_rel),
        "motivo": motivo,
        "mensaje": msg,
        "detalles": resultados_ciclos,
    }


def calcular_proyeccion_nueva(
    db: Session,
    usuario: Usuario,
    fecha_referencia: date | None = None,
    ciclo_evaluado: date | None = None,
) -> dict[str, Any]:
    """Proyección de gastos con distribución de probabilidad calibrada y descomposición en 3 partes.

    Descomposición:
    1. CIERTO: Cuotas que vencen en el ciclo, suscripciones a cobrar y compromisos maduros detectados.
    2. RECURRENTE YA OCURRIDO: Lo pagado en este ciclo de compromisos/cuotas/suscripciones. No se extrapola.
    3. VARIABLE: Consumos no recurrentes. Estimación robusta descartando decil superior (IBM Research)
       y cuantiles empíricos calibrados sobre la propia historia del usuario.
    """
    if ciclo_evaluado is not None:
        inicio, fin = get_ciclo_fechas(usuario, ciclo_evaluado)
        hoy = inicio
        fecha_corte = inicio - timedelta(days=1)
        data = _carga(db, usuario, fecha_corte)
        anteriores = _ciclos_anteriores(usuario, inicio, 12)
        dias_totales = Decimal((fin - inicio).days + 1)
        dias_transcurridos = ZERO
        dias_restantes = dias_totales
    else:
        data = _carga(db, usuario, fecha_referencia)
        hoy = data["hoy"]
        inicio, fin = get_ciclo_fechas(usuario, hoy)
        anteriores = _ciclos_anteriores(usuario, hoy, 12)
        dias_totales = Decimal((fin - inicio).days + 1)
        dias_transcurridos = Decimal(max(0, min(int(dias_totales), (hoy - inicio).days + 1)))
        dias_restantes = Decimal(max(0, (fin - hoy).days))

    comprometidos_externos = [
        *(cuota for cuota, _ in data["cuotas"] if not cuota.pagada and cuota.fecha_vencimiento >= hoy),
        *data["suscripciones"],
    ]
    clasificacion = clasificar_gastos(data["txs"], anteriores, data["ipc"], hoy, comprometidos_externos)
    resultado: dict[str, Any] = {}

    # IDs de transacciones de compromisos
    compromiso_tx_ids = {
        tx_id
        for s in clasificacion.streams
        if s.clase == "COMPROMISO"
        for tx_id in s.transacciones_ids
    }
    # Incluir recurrentes declaradas
    compromiso_tx_ids.update(
        tx.id for tx in data["txs"]
        if getattr(tx, "es_recurrente", False) or getattr(tx, "recurrente_id", None) is not None
    )

    for moneda in (Moneda.ARS, Moneda.USD):
        # 1. Identificar ciclos anteriores que tuvieron datos en esta moneda
        ciclos_con_datos_moneda = [
            (c_ini, c_fin) for c_ini, c_fin in anteriores
            if any(c_ini <= tx.fecha <= c_fin and tx.moneda == moneda for tx in data["txs"])
        ]
        cant_ciclos_datos = len(ciclos_con_datos_moneda)

        # Puerta de la escalera de historia
        datos_suficientes, nivel_confianza, mensaje_insuficiente = evaluar_escalera_historia(cant_ciclos_datos)

        # ----------------------------------------------------------------------
        # PARTE 1: CIERTO (Pendiente en lo que queda del ciclo)
        # ----------------------------------------------------------------------
        # Cuotas de crédito pendientes de pago que vencen en [hoy, fin]
        cuotas_pendientes = sum(
            (c.monto_real or c.monto_proyectado or ZERO
             for c, grupo in data["cuotas"]
             if grupo.moneda == moneda and not c.pagada and hoy <= c.fecha_vencimiento <= fin),
            ZERO,
        )
        # Suscripciones activas que se cobrarán en [hoy, fin]
        subs_pendientes = sum(
            (h.monto for h in data["historial_subs"]
             if h.moneda == moneda and any(s.id == h.suscripcion_id and hoy <= s.proximo_cobro <= fin for s in data["suscripciones"])),
            ZERO,
        )
        # Compromisos maduros detectados con fecha esperada en [hoy, fin]
        compr_maduros_pendientes = ZERO
        for s in clasificacion.streams:
            if s.clase == "COMPROMISO" and s.estado == "MADURO" and s.moneda == moneda and s.senal != "DECLARADO":
                if s.proxima_fecha_esperada and hoy <= s.proxima_fecha_esperada <= fin:
                    # Verificar que no se haya pagado ya en este ciclo
                    ya_pagado = any(inicio <= tx.fecha <= hoy and tx.id in s.transacciones_ids for tx in data["txs"])
                    if not ya_pagado:
                        compr_maduros_pendientes += s.monto_mediano_deflactado

        cierto_total = cuotas_pendientes + subs_pendientes + compr_maduros_pendientes

        # ----------------------------------------------------------------------
        # PARTE 2: RECURRENTE YA OCURRIDO EN ESTE CICLO
        # ----------------------------------------------------------------------
        txs_ciclo_actual = [
            tx for tx in data["txs"]
            if inicio <= tx.fecha <= hoy and tx.moneda == moneda and es_gasto_consumo(tx)
        ]
        txs_compr_ocurridas = [tx for tx in txs_ciclo_actual if tx.id in compromiso_tx_ids]
        recurrente_ya_ocurrido = sum((tx.monto for tx in txs_compr_ocurridas), ZERO)

        # ----------------------------------------------------------------------
        # PARTE 3: VARIABLE (Ocurrido en este ciclo + Proyectado restante)
        # ----------------------------------------------------------------------
        txs_var_ocurridas = [tx for tx in txs_ciclo_actual if tx.id not in compromiso_tx_ids]
        var_actual = sum((tx.monto for tx in txs_var_ocurridas), ZERO)

        # Desglose por categoría en el ciclo actual
        actuales_por_cat: dict[Any, Decimal] = {}
        for tx in txs_ciclo_actual:
            actuales_por_cat[tx.categoria_id] = actuales_por_cat.get(tx.categoria_id, ZERO) + tx.monto

        advertencias: list[str] = []
        if mensaje_insuficiente:
            advertencias.append(mensaje_insuficiente)

        # Variables históricas por ciclo con descarte del decil superior (IBM Research)
        vars_por_ciclo: list[Decimal] = []
        bases_diarias_hist: list[Decimal] = []
        shocks_hist: list[Decimal] = []

        for cp_ini, cp_fin in ciclos_con_datos_moneda:
            txs_c = [
                tx for tx in data["txs"]
                if cp_ini <= tx.fecha <= cp_fin and tx.moneda == moneda and es_gasto_consumo(tx)
            ]
            txs_v = [tx for tx in txs_c if tx.id not in compromiso_tx_ids]
            dias_c = Decimal((cp_fin - cp_ini).days + 1)
            t_base, tot_base, tot_shock = estimar_gasto_diario_basico_robusto(
                txs_v, dias_c, fin, data["ipc"], moneda
            )
            tot_v = tot_base + tot_shock
            vars_por_ciclo.append(tot_v)
            bases_diarias_hist.append(t_base)
            shocks_hist.append(tot_shock)

        # Comprobar estacionalidad (dejada implementada y apagada)
        ciclos_estac = [(c[0], c[1], v) for c, v in zip(ciclos_con_datos_moneda, vars_por_ciclo)]
        estac_activa, _, estac_motivo = detectar_y_ajustar_estacionalidad(ciclos_estac, fin.month)
        if not estac_activa and cant_ciclos_datos >= 3 and moneda == Moneda.ARS:
            advertencias.append(estac_motivo)

        # ----------------------------------------------------------------------
        # EVALUACIÓN DE LA PUERTA DE CALIBRACIÓN INDIVIDUAL POR USUARIO
        # ----------------------------------------------------------------------
        calib: dict[str, Any] | None = None
        if ciclo_evaluado is None:
            calib = evaluar_calibracion_usuario(data, usuario, moneda, compromiso_tx_ids)

        ciclos_con_variable = [v for v in vars_por_ciclo if v > ZERO]
        no_pasa_puerta = (calib is not None and not calib["pasa_puerta"])
        if not datos_suficientes or len(ciclos_con_variable) < 3 or no_pasa_puerta:
            # Caso no calibrado o datos insuficientes: solo se muestra lo CIERTO + lo ya ocurrido.
            total_sin_historia = cierto_total + recurrente_ya_ocurrido + var_actual
            mensaje_cierre = (
                (calib["mensaje"] if calib else None)
                or mensaje_insuficiente
                or "Mostramos tus compromisos ciertos (cuotas y suscripciones). No registrás gastos variables en suficientes ciclos anteriores para calibrar una distribución de probabilidad."
            )
            advertencias_salida = list(advertencias)
            if mensaje_cierre not in advertencias_salida:
                advertencias_salida.append(mensaje_cierre)

            categorias = []
            for cat_id, monto in actuales_por_cat.items():
                cat_nom = next((getattr(tx.categoria, "nombre", None) for tx in data["txs"] if tx.categoria_id == cat_id), "Sin categoría")
                categorias.append({
                    "categoria_id": str(cat_id) if cat_id else None,
                    "categoria_nombre": cat_nom,
                    "gasto_actual_ciclo": float(monto),
                    "promedio_historico": float(monto),
                    "proyectado": float(monto),
                    "rango_piso": float(monto),
                    "rango_techo": float(monto),
                    "fuera_de_patron": False,
                })

            ingreso_actual = _suma_deflactada([tx for tx in data["txs"] if inicio <= tx.fecha <= hoy], data["ipc"], hoy, moneda, TipoTransaccion.INGRESO)
            resultado[moneda.value.lower()] = {
                "periodo": {
                    "fecha_inicio": inicio.isoformat(),
                    "fecha_fin": fin.isoformat(),
                    "dias_transcurridos": int(dias_transcurridos),
                    "dias_restantes": int(dias_restantes),
                    "dias_totales": int(dias_totales),
                },
                "gasto_proyectado_total": float(total_sin_historia),
                "rango": {
                    "piso": float(total_sin_historia),
                    "central": float(total_sin_historia),
                    "techo": float(total_sin_historia),
                },
                "rango_poco_informativo": False,
                "balance_proyectado": float(ingreso_actual - total_sin_historia),
                "ingresos_proyectados": float(ingreso_actual),
                "certezas": {
                    "cuotas_restantes": float(cuotas_pendientes),
                    "suscripciones_restantes": float(subs_pendientes),
                    "total": float(cierto_total),
                },
                "desglose_por_categoria": categorias,
                "nivel_confianza": nivel_confianza,
                "ciclos_analizados": cant_ciclos_datos,
                "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
                "advertencias": advertencias_salida,
                "datos_suficientes": False,
                "clasificacion": {
                    "comprometidos": len(clasificacion.comprometidos),
                    "recurrentes_detectados": len(clasificacion.recurrentes_detectados),
                    "variables": len(clasificacion.variables),
                },
                "distribucion": None,
                "intervalos": None,
                "descomposicion": {
                    "cierto": float(cierto_total),
                    "recurrente_ya_ocurrido": float(recurrente_ya_ocurrido),
                    "variable_proyectado": float(var_actual),
                },
                "mensaje_insuficiente": mensaje_cierre,
                "calibracion": {
                    "pasa_puerta": calib["pasa_puerta"] if calib else False,
                    "ciclos_evaluados": calib["ciclos_evaluados"] if calib else 0,
                    "cobertura_50": calib["cobertura_50"] if calib else None,
                    "cobertura_80": calib["cobertura_80"] if calib else None,
                    "cobertura_95": calib["cobertura_95"] if calib else None,
                    "ancho_medio_80_rel": calib["ancho_medio_80_rel"] if calib else None,
                    "motivo": calib["motivo"] if calib else "pocos_ciclos",
                    "mensaje": mensaje_cierre,
                } if calib else None,
            }
            continue

        # Proyección probabilística para datos suficientes (>= 3 ciclos con gastos variables y puerta superada)
        fraccion_restante = dias_restantes / dias_totales if dias_totales > ZERO else ZERO

        # Simulación de gasto variable restante escalando cada ciclo histórico previo
        simulaciones_var_total: list[Decimal] = []
        for v_hist in vars_por_ciclo:
            v_restante_sim = v_hist * fraccion_restante
            simulaciones_var_total.append(var_actual + v_restante_sim)

        K = len(simulaciones_var_total)
        # Ponderación por recencia: decay = 0.70 por ciclo hacia atrás
        decay = Decimal("0.70")
        weights = [decay ** Decimal(K - 1 - i) for i in range(K)]

        # Cuantiles empíricos ponderados sobre la distribución del usuario
        med_var = weighted_quantile(simulaciones_var_total, weights, Decimal("0.50"))
        p25_var = weighted_quantile(simulaciones_var_total, weights, Decimal("0.25"))
        p75_var = weighted_quantile(simulaciones_var_total, weights, Decimal("0.75"))
        p10_var = weighted_quantile(simulaciones_var_total, weights, Decimal("0.10"))
        p90_var = weighted_quantile(simulaciones_var_total, weights, Decimal("0.90"))

        mad_var = mad(simulaciones_var_total) or ZERO
        sigma_robusta = mad_var * Decimal("1.4826")
        iqr_disp = (p75_var - p25_var) / Decimal("1.349") if p75_var > p25_var else ZERO
        p90_disp = (p90_var - p10_var) / Decimal("2.563") if p90_var > p10_var else ZERO
        dispersion = max(sigma_robusta, iqr_disp, p90_disp)

        # Criterio de intervalos no declarables: si la dispersión es nula o el rango es degenerado
        if dispersion <= ZERO or (p90_var - p10_var) <= ZERO:
            total_sin_disp = cierto_total + recurrente_ya_ocurrido + var_actual
            msg_no_disp = "Mostramos tus compromisos ciertos (cuotas y suscripciones). No registrás gastos variables en suficientes ciclos anteriores para calibrar una distribución de probabilidad."
            advertencias_sin_disp = list(advertencias)
            if msg_no_disp not in advertencias_sin_disp:
                advertencias_sin_disp.append(msg_no_disp)

            categorias = []
            for cat_id, monto in actuales_por_cat.items():
                cat_nom = next((getattr(tx.categoria, "nombre", None) for tx in data["txs"] if tx.categoria_id == cat_id), "Sin categoría")
                categorias.append({
                    "categoria_id": str(cat_id) if cat_id else None,
                    "categoria_nombre": cat_nom,
                    "gasto_actual_ciclo": float(monto),
                    "promedio_historico": float(monto),
                    "proyectado": float(monto),
                    "rango_piso": float(monto),
                    "rango_techo": float(monto),
                    "fuera_de_patron": False,
                })

            ingreso_actual = _suma_deflactada([tx for tx in data["txs"] if inicio <= tx.fecha <= hoy], data["ipc"], hoy, moneda, TipoTransaccion.INGRESO)
            resultado[moneda.value.lower()] = {
                "periodo": {
                    "fecha_inicio": inicio.isoformat(),
                    "fecha_fin": fin.isoformat(),
                    "dias_transcurridos": int(dias_transcurridos),
                    "dias_restantes": int(dias_restantes),
                    "dias_totales": int(dias_totales),
                },
                "gasto_proyectado_total": float(total_sin_disp),
                "rango": {
                    "piso": float(total_sin_disp),
                    "central": float(total_sin_disp),
                    "techo": float(total_sin_disp),
                },
                "rango_poco_informativo": False,
                "balance_proyectado": float(ingreso_actual - total_sin_disp),
                "ingresos_proyectados": float(ingreso_actual),
                "certezas": {
                    "cuotas_restantes": float(cuotas_pendientes),
                    "suscripciones_restantes": float(subs_pendientes),
                    "total": float(cierto_total),
                },
                "desglose_por_categoria": categorias,
                "nivel_confianza": nivel_confianza,
                "ciclos_analizados": cant_ciclos_datos,
                "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
                "advertencias": advertencias_sin_disp,
                "datos_suficientes": False,
                "clasificacion": {
                    "comprometidos": len(clasificacion.comprometidos),
                    "recurrentes_detectados": len(clasificacion.recurrentes_detectados),
                    "variables": len(clasificacion.variables),
                },
                "distribucion": None,
                "intervalos": None,
                "descomposicion": {
                    "cierto": float(cierto_total),
                    "recurrente_ya_ocurrido": float(recurrente_ya_ocurrido),
                    "variable_proyectado": float(var_actual),
                },
                "mensaje_insuficiente": msg_no_disp,
                "calibracion": {
                    "pasa_puerta": calib["pasa_puerta"] if calib else False,
                    "ciclos_evaluados": calib["ciclos_evaluados"] if calib else 0,
                    "cobertura_50": calib["cobertura_50"] if calib else None,
                    "cobertura_80": calib["cobertura_80"] if calib else None,
                    "cobertura_95": calib["cobertura_95"] if calib else None,
                    "ancho_medio_80_rel": calib["ancho_medio_80_rel"] if calib else None,
                    "motivo": "intervalo_no_informativo",
                    "mensaje": msg_no_disp,
                } if calib else None,
            }
            continue

        df = max(1, K - 1)
        t_50 = student_t_critical(df, "50")
        t_80 = student_t_critical(df, "80")
        t_95 = student_t_critical(df, "95")
        factor_muestra = Decimal(str((1 + 1 / max(1, K)) ** 0.5))

        # Base cierta consolidada (lo comprometido pendiente + lo recurrente ya pagado)
        base_compromisos = cierto_total + recurrente_ya_ocurrido

        # Valor central: Mediana (q50)
        q50 = max(ZERO, base_compromisos + med_var)

        # Intervalo al 50%: [q25, q75] acotado en cero
        q25 = max(ZERO, min(q50, base_compromisos + min(p25_var, med_var - t_50 * dispersion * factor_muestra)))
        q75 = max(q50, base_compromisos + max(p75_var, med_var + t_50 * dispersion * factor_muestra))

        # Intervalo al 80%: [q10, q90] acotado en cero
        q10 = max(ZERO, min(q25, base_compromisos + min(p10_var, med_var - t_80 * dispersion * factor_muestra)))
        q90 = max(q75, base_compromisos + max(p90_var, med_var + t_80 * dispersion * factor_muestra))

        # Intervalo al 95%: [q025, q975] acotado en cero y respetando el máximo observado
        max_obs = base_compromisos + max(simulaciones_var_total)
        q975 = max(q90, max_obs, base_compromisos + med_var + t_95 * dispersion * factor_muestra)
        q025 = max(ZERO, min(q10, base_compromisos + med_var - t_95 * dispersion * factor_muestra))

        # Control de informatividad del ciclo actual: si el ancho al 80% supera el 100% de q50
        ancho_actual_80 = q90 - q10
        ancho_actual_rel = (ancho_actual_80 / q50) if q50 > ZERO else ZERO
        if ciclo_evaluado is None and ancho_actual_rel > UMBRAL_ANCHO_MAXIMO_RELATIVO_80:
            total_sin_info = cierto_total + recurrente_ya_ocurrido + var_actual
            msg_no_info = (
                "La proyección para este ciclo no es suficientemente informativa: el rango probable "
                "supera el 100% de tu gasto proyectado. Mostramos tus compromisos ciertos."
            )
            advertencias_salida = list(advertencias)
            if msg_no_info not in advertencias_salida:
                advertencias_salida.append(msg_no_info)

            categorias = []
            for cat_id, monto in actuales_por_cat.items():
                cat_nom = next((getattr(tx.categoria, "nombre", None) for tx in data["txs"] if tx.categoria_id == cat_id), "Sin categoría")
                categorias.append({
                    "categoria_id": str(cat_id) if cat_id else None,
                    "categoria_nombre": cat_nom,
                    "gasto_actual_ciclo": float(monto),
                    "promedio_historico": float(monto),
                    "proyectado": float(monto),
                    "rango_piso": float(monto),
                    "rango_techo": float(monto),
                    "fuera_de_patron": False,
                })

            ingreso_actual = _suma_deflactada([tx for tx in data["txs"] if inicio <= tx.fecha <= hoy], data["ipc"], hoy, moneda, TipoTransaccion.INGRESO)
            resultado[moneda.value.lower()] = {
                "periodo": {
                    "fecha_inicio": inicio.isoformat(),
                    "fecha_fin": fin.isoformat(),
                    "dias_transcurridos": int(dias_transcurridos),
                    "dias_restantes": int(dias_restantes),
                    "dias_totales": int(dias_totales),
                },
                "gasto_proyectado_total": float(total_sin_info),
                "rango": {
                    "piso": float(total_sin_info),
                    "central": float(total_sin_info),
                    "techo": float(total_sin_info),
                },
                "rango_poco_informativo": True,
                "balance_proyectado": float(ingreso_actual - total_sin_info),
                "ingresos_proyectados": float(ingreso_actual),
                "certezas": {
                    "cuotas_restantes": float(cuotas_pendientes),
                    "suscripciones_restantes": float(subs_pendientes),
                    "total": float(cierto_total),
                },
                "desglose_por_categoria": categorias,
                "nivel_confianza": nivel_confianza,
                "ciclos_analizados": cant_ciclos_datos,
                "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
                "advertencias": advertencias_salida,
                "datos_suficientes": False,
                "clasificacion": {
                    "comprometidos": len(clasificacion.comprometidos),
                    "recurrentes_detectados": len(clasificacion.recurrentes_detectados),
                    "variables": len(clasificacion.variables),
                },
                "distribucion": None,
                "intervalos": None,
                "descomposicion": {
                    "cierto": float(cierto_total),
                    "recurrente_ya_ocurrido": float(recurrente_ya_ocurrido),
                    "variable_proyectado": float(var_actual),
                },
                "mensaje_insuficiente": msg_no_info,
                "calibracion": {
                    "pasa_puerta": False,
                    "ciclos_evaluados": calib["ciclos_evaluados"] if calib else 0,
                    "cobertura_50": calib["cobertura_50"] if calib else None,
                    "cobertura_80": calib["cobertura_80"] if calib else None,
                    "cobertura_95": calib["cobertura_95"] if calib else None,
                    "ancho_medio_80_rel": calib["ancho_medio_80_rel"] if calib else None,
                    "motivo": "intervalo_no_informativo",
                    "mensaje": msg_no_info,
                } if calib else None,
            }
            continue

        # Desglose por categoría para compatibilidad con UI
        historiales = _historial_categorias(data["txs"], anteriores, data["ipc"], hoy, moneda, clasificacion)
        categorias = []
        total_desglose = ZERO
        for cat_id, valores in historiales.items():
            actual_cat = actuales_por_cat.get(cat_id, ZERO)
            centro = mediana(valores) or ZERO
            p25_cat = percentil(valores, Decimal("0.25")) or centro
            p75_cat = percentil(valores, Decimal("0.75")) or centro
            presencia = len(valores) / len(anteriores) if anteriores else 0
            if presencia >= 0.60 or actual_cat > ZERO:
                proy_cat = centro
            else:
                continue
            cat_nom = next((getattr(tx.categoria, "nombre", None) for tx in data["txs"] if tx.categoria_id == cat_id), "Sin categoría")
            categorias.append({
                "categoria_id": str(cat_id) if cat_id else None,
                "categoria_nombre": cat_nom,
                "gasto_actual_ciclo": float(actual_cat),
                "promedio_historico": float(centro),
                "proyectado": float(proy_cat),
                "rango_piso": float(max(ZERO, p25_cat)),
                "rango_techo": float(p75_cat),
                "fuera_de_patron": False,
            })
            total_desglose += proy_cat

        if total_desglose > ZERO and med_var > ZERO:
            escala = med_var / total_desglose
            for c in categorias:
                c["proyectado"] = float(Decimal(str(c["proyectado"])) * escala)
                c["rango_piso"] = float(max(ZERO, Decimal(str(c["rango_piso"])) * escala))
                c["rango_techo"] = float(Decimal(str(c["rango_techo"])) * escala)

        # Proyección de ingresos (reutilizando data_previa para no duplicar consultas)
        ingreso_info = proyectar_ingreso_ciclo(db, usuario, fin, data_previa=data)
        ingreso_proy = Decimal(str(ingreso_info.get("ingreso_proyectado", "0")))

        distribucion = {
            "q025": float(q025),
            "q10": float(q10),
            "q25": float(q25),
            "q50": float(q50),
            "q75": float(q75),
            "q90": float(q90),
            "q975": float(q975),
        }
        intervalos = {
            "intervalo_50": {"piso": float(q25), "techo": float(q75)},
            "intervalo_80": {"piso": float(q10), "techo": float(q90)},
            "intervalo_95": {"piso": float(q025), "techo": float(q975)},
        }

        resultado[moneda.value.lower()] = {
            "periodo": {
                "fecha_inicio": inicio.isoformat(),
                "fecha_fin": fin.isoformat(),
                "dias_transcurridos": int(dias_transcurridos),
                "dias_restantes": int(dias_restantes),
                "dias_totales": int(dias_totales),
            },
            "gasto_proyectado_total": float(q50),
            "rango": {
                "piso": float(q10),
                "central": float(q50),
                "techo": float(q90),
            },
            "rango_poco_informativo": (q90 - q10) > q50 if q50 > ZERO else False,
            "balance_proyectado": float(ingreso_proy - q50),
            "ingresos_proyectados": float(ingreso_proy),
            "certezas": {
                "cuotas_restantes": float(cuotas_pendientes),
                "suscripciones_restantes": float(subs_pendientes),
                "total": float(cierto_total),
            },
            "desglose_por_categoria": categorias,
            "nivel_confianza": nivel_confianza,
            "ciclos_analizados": cant_ciclos_datos,
            "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
            "advertencias": advertencias,
            "datos_suficientes": True,
            "clasificacion": {
                "comprometidos": len(clasificacion.comprometidos),
                "recurrentes_detectados": len(clasificacion.recurrentes_detectados),
                "variables": len(clasificacion.variables),
            },
            "distribucion": distribucion,
            "intervalos": intervalos,
            "descomposicion": {
                "cierto": float(cierto_total),
                "recurrente_ya_ocurrido": float(recurrente_ya_ocurrido),
                "variable_proyectado": float(med_var),
            },
            "mensaje_insuficiente": None,
            "calibracion": {
                "pasa_puerta": True,
                "ciclos_evaluados": calib["ciclos_evaluados"] if calib else 0,
                "cobertura_50": calib["cobertura_50"] if calib else None,
                "cobertura_80": calib["cobertura_80"] if calib else None,
                "cobertura_95": calib["cobertura_95"] if calib else None,
                "ancho_medio_80_rel": calib["ancho_medio_80_rel"] if calib else None,
                "motivo": None,
                "mensaje": None,
            } if calib else None,
        }

    return resultado


def proyectar_ingreso_ciclo(
    db: Session,
    usuario: Usuario,
    fecha_ciclo: date,
    data_previa: dict[str, Any] | None = None,
) -> dict[str, Decimal | bool]:
    """Proyecta ingresos distinguiendo certeza e incertidumbre, e incluyendo aguinaldo.

    En Argentina, el Sueldo Anual Complementario (SAC) se cobra en junio y diciembre.
    Si el ciclo objetivo es junio o diciembre, se agrega el 50% del ingreso típico mensual habitual.
    """
    data = data_previa if data_previa is not None else _carga(db, usuario, fecha_ciclo)
    inicio, fin = get_ciclo_fechas(usuario, fecha_ciclo)
    anteriores = _ciclos_anteriores(usuario, fecha_ciclo, 12)

    # Ingresos recurrentes activos ciertos
    ingreso_cierto = ZERO
    for r in data["recurrentes"]:
        if r.tipo == TipoTransaccionRecurrente.INGRESO and r.moneda == Moneda.ARS:
            ingreso_cierto += r.monto

    # Ingresos históricos observados deflactados
    ingresos_hist = []
    ingresos_por_mes = []
    for c_ini, c_fin in anteriores:
        valor = _suma_deflactada(
            [tx for tx in data["txs"] if c_ini <= tx.fecha <= c_fin],
            data["ipc"],
            fecha_ciclo,
            Moneda.ARS,
            TipoTransaccion.INGRESO,
        )
        if valor > ZERO:
            ingresos_hist.append(valor)
            ingresos_por_mes.append((c_fin.month, valor))

    # Ingreso mensual regular de referencia (excluyendo aguinaldos de junio y diciembre)
    regulares = [v for m, v in ingresos_por_mes if m not in (6, 12)]
    ingreso_regular = mediana(regulares) if regulares else (mediana(ingresos_hist) or ingreso_cierto)

    # Aguinaldo
    aguinaldo_aplica = fin.month in (6, 12) and (ingreso_regular > ZERO or ingreso_cierto > ZERO)
    aguinaldo = (ingreso_regular * Decimal("0.50")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if aguinaldo_aplica else ZERO

    base_estimada = ingreso_regular if ingreso_regular > ZERO else ingreso_cierto
    ingreso_total_proyectado = base_estimada + aguinaldo

    return {
        "fecha_inicio": inicio.isoformat(),
        "fecha_fin": fin.isoformat(),
        "ingreso_proyectado": ingreso_total_proyectado,
        "estacionalidad_aplicada": aguinaldo_aplica,
        "ciclos_estacionales": Decimal(len(ingresos_hist)),
        "ingreso_regular_referencia": base_estimada,
        "aguinaldo_incluido": aguinaldo,
    }


def backtest_probabilistico_ciclo(
    db: Session,
    usuario: Usuario,
    fecha_ciclo: date,
) -> dict[str, Any]:
    """Evalúa la proyección probabilística en un ciclo pasado usando estrictamente datos anteriores.

    Calcula:
    - Valor real deflactado.
    - Cuantil central (q50).
    - Intervalos calibrados al 50%, 80% y 95%.
    - Cobertura empírica binaria para cada nivel.
    - Pinball loss promediada sobre grilla de 19 cuantiles (tau in [0.05, 0.95]).
    - Error absoluto medio del valor central.
    """
    inicio, fin = get_ciclo_fechas(usuario, fecha_ciclo)
    ipc = db.execute(select(IPCCache).order_by(IPCCache.fecha_dato)).scalars().all()

    # Transacciones completas para obtener el gasto real del ciclo evaluado
    txs_eval = db.execute(
        select(Transaccion)
        .options(
            joinedload(Transaccion.categoria),
            joinedload(Transaccion.subcategoria),
            joinedload(Transaccion.billetera),
        )
        .where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha >= inicio,
            Transaccion.fecha <= fin,
        )
    ).scalars().all()
    gc_real = gasto_ciclo(txs_eval, inicio, fin, fin, ipc, Moneda.ARS)
    y_real = gc_real.deflactado

    # Correr la proyección probabilística evaluada para el ciclo completo con historial previo
    proy = calcular_proyeccion_nueva(db, usuario, ciclo_evaluado=fecha_ciclo)
    p_ars = proy.get("ars", {})

    q50 = Decimal(str(p_ars.get("gasto_proyectado_total", 0)))
    dist = p_ars.get("distribucion") or {}
    interv = p_ars.get("intervalos") or {}

    q025 = Decimal(str(dist.get("q025", q50)))
    q10 = Decimal(str(dist.get("q10", q50)))
    q25 = Decimal(str(dist.get("q25", q50)))
    q75 = Decimal(str(dist.get("q75", q50)))
    q90 = Decimal(str(dist.get("q90", q50)))
    q975 = Decimal(str(dist.get("q975", q50)))

    cubierto_50 = (q25 <= y_real <= q75)
    cubierto_80 = (q10 <= y_real <= q90)
    cubierto_95 = (q025 <= y_real <= q975)

    err_abs = abs(q50 - y_real)

    # Pinball loss en grilla de 19 cuantiles tau in [0.05, 0.95]
    grid_taus = [Decimal(str(round(x * 0.05, 2))) for x in range(1, 20)]
    pb_losses = []
    # Reconstruir cuantiles aproximados por interpolación
    cuantiles_clave = [
        (Decimal("0.025"), q025),
        (Decimal("0.10"), q10),
        (Decimal("0.25"), q25),
        (Decimal("0.50"), q50),
        (Decimal("0.75"), q75),
        (Decimal("0.90"), q90),
        (Decimal("0.975"), q975),
    ]
    for tau in grid_taus:
        # Interpolación lineal entre cuantiles clave
        if tau <= Decimal("0.025"):
            q_tau = q025
        elif tau >= Decimal("0.975"):
            q_tau = q975
        else:
            # Encontrar segmento
            for i in range(len(cuantiles_clave) - 1):
                t1, v1 = cuantiles_clave[i]
                t2, v2 = cuantiles_clave[i + 1]
                if t1 <= tau <= t2:
                    frac = (tau - t1) / (t2 - t1)
                    q_tau = v1 + (v2 - v1) * frac
                    break
        pb_losses.append(pinball_loss(y_real, q_tau, tau))

    avg_pinball_loss = sum(pb_losses, ZERO) / Decimal(len(pb_losses))

    return {
        "ciclo": fin.strftime("%Y-%m"),
        "real": y_real,
        "q50": q50,
        "intervalo_50": {"piso": q25, "techo": q75, "cubierto": cubierto_50},
        "intervalo_80": {"piso": q10, "techo": q90, "cubierto": cubierto_80},
        "intervalo_95": {"piso": q025, "techo": q975, "cubierto": cubierto_95},
        "ancho_80": q90 - q10,
        "ancho_95": q975 - q025,
        "error_absoluto": err_abs,
        "pinball_loss": avg_pinball_loss,
        "datos_suficientes": p_ars.get("datos_suficientes", False),
    }


def backtest_viejo_ciclo(db: Session, usuario: Usuario, fecha_ciclo: date) -> dict[str, Any]:
    """Evalúa la proyección vieja real en una fecha pasada."""
    inicio, fin = get_ciclo_fechas(usuario, fecha_ciclo)
    fecha_corte = inicio - timedelta(days=1)
    data = _carga(db, usuario, fecha_referencia=fecha_corte)
    ipc = data["ipc"]
    anteriores = _ciclos_anteriores(usuario, fecha_corte, 12)

    # Gasto real
    txs_eval = db.execute(
        select(Transaccion)
        .options(
            joinedload(Transaccion.categoria),
            joinedload(Transaccion.subcategoria),
            joinedload(Transaccion.billetera),
        )
        .where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha >= inicio,
            Transaccion.fecha <= fin,
        )
    ).scalars().all()
    gc_real = gasto_ciclo(txs_eval, inicio, fin, fin, ipc, Moneda.ARS)
    y_real = gc_real.deflactado

    ciclos_totales = [
        _suma_deflactada([tx for tx in data["txs"] if a <= tx.fecha <= b], ipc, fin, Moneda.ARS, TipoTransaccion.EGRESO)
        for a, b in anteriores
    ]
    ciclos_totales = [v for v in ciclos_totales if v > ZERO]
    cuotas = sum(
        (c.monto_real or c.monto_proyectado or ZERO
         for c, grupo in data["cuotas"]
         if grupo.moneda == Moneda.ARS and not c.pagada and inicio <= c.fecha_vencimiento <= fin),
        ZERO,
    )
    subs = sum(
        (h.monto for h in data["historial_subs"]
         if h.moneda == Moneda.ARS and any(s.id == h.suscripcion_id and inicio <= s.proximo_cobro <= fin for s in data["suscripciones"])),
        ZERO,
    )
    total_compr = cuotas + subs
    variable_central = mediana(ciclos_totales) or ZERO
    total = variable_central + total_compr

    dispersion_mad = mad(ciclos_totales) or ZERO
    dispersion_iqr = ((percentil(ciclos_totales, Decimal("0.75")) or variable_central) - (percentil(ciclos_totales, Decimal("0.25")) or variable_central)) / Decimal("2")
    dispersion_p10_p90 = ((percentil(ciclos_totales, Decimal("0.90")) or variable_central) - (percentil(ciclos_totales, Decimal("0.10")) or variable_central)) / Decimal("2")
    dispersion = max(dispersion_mad, dispersion_iqr, dispersion_p10_p90)
    piso = max(ZERO, total - dispersion)
    techo = total + dispersion

    cubierto = (piso <= y_real <= techo)
    err_abs = abs(total - y_real)

    # Pinball loss del método viejo sobre la misma grilla de 19 cuantiles
    grid_taus = [Decimal(str(round(x * 0.05, 2))) for x in range(1, 20)]
    pb_losses_viejo = []
    for tau in grid_taus:
        q_tau = max(ZERO, total + (tau - Decimal("0.5")) * Decimal("2") * dispersion)
        pb_losses_viejo.append(pinball_loss(y_real, q_tau, tau))
    avg_pb_viejo = sum(pb_losses_viejo, ZERO) / Decimal(len(pb_losses_viejo))

    return {
        "ciclo": fin.strftime("%Y-%m"),
        "real": y_real,
        "central": total,
        "piso": piso,
        "techo": techo,
        "ancho": techo - piso,
        "cubierto": cubierto,
        "error_absoluto": err_abs,
        "pinball_loss": avg_pb_viejo,
    }


def backtest_ciclo(db: Session, usuario: Usuario, fecha_ciclo: date) -> dict[str, Decimal | str | bool]:
    """Mantenido para compatibilidad con scripts existentes.

    Compara la proyección probabilística nueva con el método viejo.
    """
    nuevo_res = backtest_probabilistico_ciclo(db, usuario, fecha_ciclo)
    viejo_res = backtest_viejo_ciclo(db, usuario, fecha_ciclo)

    real = nuevo_res["real"]
    nuevo = nuevo_res["q50"]
    viejo = viejo_res["central"]
    error_nuevo = (abs(nuevo - real) / real * Decimal("100")) if real > ZERO else ZERO
    error_viejo = (abs(viejo - real) / real * Decimal("100")) if real > ZERO else ZERO

    i80 = nuevo_res["intervalo_80"]
    return {
        "ciclo": nuevo_res["ciclo"],
        "real": real,
        "nuevo": nuevo,
        "error_nuevo": error_nuevo,
        "viejo": viejo,
        "error_viejo": error_viejo,
        "rango_piso": i80["piso"],
        "rango_techo": i80["techo"],
        "rango_contiene_real": i80["cubierto"],
    }

