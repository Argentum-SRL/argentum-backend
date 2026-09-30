"""
Servicio de proyecciones financieras de Argentum.

Implementa el motor probabilístico calibrado y la proyección financiera.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.compromisos_service import calcular_compromisos_memoria, obtener_precio_vigente_memoria
from app.services.dashboard_service import get_ciclo_fechas
from app.services.datos_motor_service import cargar_datos_motor, ciclos_anteriores
from app.services.definiciones_service import ContextoDefiniciones, es_gasto, es_ingreso
from app.services.ingreso_habitual_service import (
    calcular_ingreso_esperado_ciclo,
    obtener_ingreso_habitual,
)
from app.utils.fecha import hoy_argentina
from app.utils.finanzas import (
    ClasificacionGasto,
    MINIMO_CICLOS_EVALUABLES_CALIBRACION,
    ONE,
    StreamRecurrente,
    UMBRAL_ANCHO_MAXIMO_RELATIVO_80,
    ZERO,
    _indice_por_mes,
    clasificar_gastos,
    deflactar_monto,
    estimar_gasto_diario_basico_robusto,
    gasto_ciclo,
    mad,
    mediana,
    monto_mensual_deflactado_stream,
    percentil,
    posicion_relativa,
    student_t_critical,
    weighted_quantile,
)


def _historial_categorias(txs: list[Any], ciclos: list[tuple[date, date]], ipc: Any, destino: date, moneda: Moneda, clasificacion: ClasificacionGasto) -> dict[Any, list[Decimal]]:
    resultado: dict[Any, list[Decimal]] = {}
    ipc_map = ipc if isinstance(ipc, dict) else _indice_por_mes(ipc)
    variables = set(clasificacion.variables) | set(clasificacion.recurrentes_detectados)
    for inicio, fin in ciclos:
        por_cat: dict[Any, Decimal] = {}
        for tx in txs:
            if tx not in variables or tx.moneda != moneda or not (inicio <= tx.fecha <= fin):
                continue
            valor = deflactar_monto(tx.monto, tx.fecha, destino, ipc_map, moneda).monto
            por_cat[tx.categoria_id] = por_cat.get(tx.categoria_id, ZERO) + valor
        for cat, valor in por_cat.items():
            resultado.setdefault(cat, []).append(valor)
    return resultado



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
        data = cargar_datos_motor(db, usuario, fecha_corte)
        anteriores = ciclos_anteriores(usuario, inicio, 12)
        dias_totales = Decimal((fin - inicio).days + 1)
        dias_transcurridos = ZERO
        dias_restantes = dias_totales
    else:
        data = cargar_datos_motor(db, usuario, fecha_referencia)
        hoy = data["hoy"]
        inicio, fin = get_ciclo_fechas(usuario, hoy)
        anteriores = ciclos_anteriores(usuario, hoy, 12)
        dias_totales = Decimal((fin - inicio).days + 1)
        dias_transcurridos = Decimal(max(0, min(int(dias_totales), (hoy - inicio).days + 1)))
        dias_restantes = Decimal(max(0, (fin - hoy).days))

    comprometidos_externos = [
        *(cuota for cuota, _ in data["cuotas"] if not cuota.pagada and cuota.fecha_vencimiento >= hoy),
        *data["suscripciones"],
    ]
    ctx = data["ctx"]
    clasificacion = clasificar_gastos(data["txs"], anteriores, data["ipc"], hoy, comprometidos_externos, ctx=ctx)
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
        if getattr(tx, "suscripcion_id", None) is not None
    )

    for moneda in (Moneda.ARS, Moneda.USD):
        # 1. Identificar ciclos anteriores que tuvieron datos en esta moneda
        ciclos_con_datos_moneda = [
            (c_ini, c_fin) for c_ini, c_fin in anteriores
            if any(c_ini <= tx.fecha <= c_fin and tx.moneda == moneda for tx in data["txs"])
        ]
        cant_ciclos_datos = len(ciclos_con_datos_moneda)
        cerrados_con_datos = [c for c in ciclos_con_datos_moneda if c[1] < hoy]
        cant_ciclos_cerrados = len(cerrados_con_datos)

        # Puerta de la escalera de historia
        datos_suficientes, nivel_confianza, mensaje_insuficiente = evaluar_escalera_historia(cant_ciclos_datos)
        mostrar_card_moneda = bool(datos_suficientes)

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
        # Suscripciones activas que se cobrarán en [hoy, fin] a su precio vigente (Fase 2c)
        moneda_s_str = moneda.value if hasattr(moneda, "value") else str(moneda)
        subs_pendientes = sum(
            (Decimal(str(pv.monto)) for s in data["suscripciones"] if hoy <= s.proximo_cobro <= fin
             and (pv := obtener_precio_vigente_memoria(s.id, data["historial_subs"], hoy, moneda_s_str)) is not None),
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
            if inicio <= tx.fecha <= hoy and tx.moneda == moneda and es_gasto(tx, ctx)
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
                if cp_ini <= tx.fecha <= cp_fin and tx.moneda == moneda and es_gasto(tx, ctx)
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
        # (Lectura de fila guardada; el backtest nunca corre dentro del pedido)
        # ----------------------------------------------------------------------
        calib: dict[str, Any] | None = None
        if ciclo_evaluado is None:
            if cant_ciclos_cerrados < MINIMO_CICLOS_EVALUABLES_CALIBRACION:
                calib = {
                    "pasa_puerta": False,
                    "ciclos_evaluados": cant_ciclos_cerrados,
                    "cobertura_50": None,
                    "cobertura_80": None,
                    "cobertura_95": None,
                    "ancho_medio_80_rel": None,
                    "motivo": "pocos_ciclos",
                    "mensaje": (
                        f"Tu historial cuenta con {cant_ciclos_cerrados} ciclos evaluables (se requieren al menos "
                        f"{MINIMO_CICLOS_EVALUABLES_CALIBRACION} ciclos cerrados con proyección). Mostramos tus "
                        f"compromisos ciertos (cuotas y suscripciones). La proyección probabilística se activará "
                        f"cuando acumules suficiente historia para validar su calibración."
                    ),
                    "detalles": [],
                }
            else:
                from app.models.calibracion_usuario import CalibracionUsuario
                moneda_str = moneda.value if hasattr(moneda, "value") else str(moneda)
                fila_calib = db.query(CalibracionUsuario).filter(
                    CalibracionUsuario.usuario_id == usuario.id,
                    CalibracionUsuario.moneda == moneda_str,
                ).first()

                if fila_calib and fila_calib.inicio_ciclo == inicio:
                    calib = {
                        "pasa_puerta": fila_calib.pasa_puerta,
                        "ciclos_evaluados": fila_calib.ciclos_evaluados,
                        "cobertura_50": float(fila_calib.cobertura_50) if fila_calib.cobertura_50 is not None else None,
                        "cobertura_80": float(fila_calib.cobertura_80) if fila_calib.cobertura_80 is not None else None,
                        "cobertura_95": float(fila_calib.cobertura_95) if fila_calib.cobertura_95 is not None else None,
                        "ancho_medio_80_rel": float(fila_calib.ancho_medio_80_rel) if fila_calib.ancho_medio_80_rel is not None else None,
                        "motivo": fila_calib.motivo,
                        "mensaje": fila_calib.mensaje,
                        "detalles": fila_calib.detalles or [],
                    }
                else:
                    calib = {
                        "pasa_puerta": False,
                        "ciclos_evaluados": cant_ciclos_cerrados,
                        "cobertura_50": None,
                        "cobertura_80": None,
                        "cobertura_95": None,
                        "ancho_medio_80_rel": None,
                        "motivo": "calibracion_pendiente",
                        "mensaje": "La proyección se está preparando. Mostramos tus compromisos ciertos hasta que se complete la calibración.",
                        "detalles": [],
                    }

        ciclos_con_variable = [v for v in vars_por_ciclo if v > ZERO]
        no_pasa_puerta = (calib is not None and not calib["pasa_puerta"])
        if not datos_suficientes or len(ciclos_con_variable) < 3 or no_pasa_puerta:
            # Caso no calibrado o puerta cerrada: solo periodo, certezas, calibración y mensaje; lo demás en null.
            mensaje_cierre = (
                (calib["mensaje"] if calib else None)
                or mensaje_insuficiente
                or "Mostramos tus compromisos ciertos (cuotas y suscripciones). No registrás gastos variables en suficientes ciclos anteriores para calibrar una distribución de probabilidad."
            )
            advertencias_salida = list(advertencias)
            if mensaje_cierre not in advertencias_salida:
                advertencias_salida.append(mensaje_cierre)

            # Si es ciclo_evaluado (backtest), mantenemos el valor numérico para que el backtest funcione idéntico
            if ciclo_evaluado is not None:
                total_sin_historia = cierto_total + recurrente_ya_ocurrido + var_actual
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
                ingreso_esp = calcular_ingreso_esperado_ciclo(
                    db, usuario, inicio, fin, moneda, hoy=hoy, ctx=data["ctx"], txs_previa=data["txs"]
                )
                if ingreso_esp is None:
                    ingreso_proy_val = None
                    balance_proy_val = None
                    msg_cobros = "Para calcular cómo terminás el ciclo necesitamos que cargues tus cobros."
                    if msg_cobros not in advertencias_salida:
                        advertencias_salida.append(msg_cobros)
                else:
                    ingreso_proy_val = float(ingreso_esp)
                    balance_proy_val = float(ingreso_esp - total_sin_historia)

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
                    "balance_proyectado": balance_proy_val,
                    "ingresos_proyectados": ingreso_proy_val,
                    "certezas": {
                        "cuotas_restantes": float(cuotas_pendientes),
                        "suscripciones_restantes": float(subs_pendientes),
                        "compromisos_restantes": float(compr_maduros_pendientes),
                        "total": float(cierto_total),
                    },
                    "desglose_por_categoria": categorias,
                    "nivel_confianza": nivel_confianza,
                    "ciclos_analizados": cant_ciclos_datos,
                    "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
                    "advertencias": advertencias_salida,
                    "datos_suficientes": False,
                    "mostrar_card": mostrar_card_moneda,
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
                    "mensaje": mensaje_cierre,
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
            else:
                # Con la puerta cerrada, por cualquier motivo, la API devuelve solo periodo, certezas, calibración y mensaje; lo demás en null.
                resultado[moneda.value.lower()] = {
                    "periodo": {
                        "fecha_inicio": inicio.isoformat(),
                        "fecha_fin": fin.isoformat(),
                        "dias_transcurridos": int(dias_transcurridos),
                        "dias_restantes": int(dias_restantes),
                        "dias_totales": int(dias_totales),
                    },
                    "gasto_proyectado_total": None,
                    "rango": None,
                    "rango_poco_informativo": None,
                    "balance_proyectado": None,
                    "ingresos_proyectados": None,
                    "certezas": {
                        "cuotas_restantes": float(cuotas_pendientes),
                        "suscripciones_restantes": float(subs_pendientes),
                        "compromisos_restantes": float(compr_maduros_pendientes),
                        "total": float(cierto_total),
                    },
                    "desglose_por_categoria": None,
                    "nivel_confianza": nivel_confianza,
                    "ciclos_analizados": cant_ciclos_datos,
                    "pesos": None,
                    "advertencias": advertencias_salida,
                    "datos_suficientes": False,
                    "mostrar_card": mostrar_card_moneda,
                    "clasificacion": None,
                    "distribucion": None,
                    "intervalos": None,
                    "descomposicion": {
                        "cierto": float(cierto_total),
                        "recurrente_ya_ocurrido": float(recurrente_ya_ocurrido),
                        "variable_proyectado": None,
                    },
                    "mensaje_insuficiente": mensaje_cierre,
                    "mensaje": mensaje_cierre,
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

            if ciclo_evaluado is not None:
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

                ingreso_esp = calcular_ingreso_esperado_ciclo(
                    db, usuario, inicio, fin, moneda, hoy=hoy, ctx=data["ctx"], txs_previa=data["txs"]
                )
                if ingreso_esp is None:
                    ingreso_proy_val = None
                    balance_proy_val = None
                    msg_cobros = "Para calcular cómo terminás el ciclo necesitamos que cargues tus cobros."
                    if msg_cobros not in advertencias_sin_disp:
                        advertencias_sin_disp.append(msg_cobros)
                else:
                    ingreso_proy_val = float(ingreso_esp)
                    balance_proy_val = float(ingreso_esp - total_sin_disp)

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
                    "balance_proyectado": balance_proy_val,
                    "ingresos_proyectados": ingreso_proy_val,
                    "certezas": {
                        "cuotas_restantes": float(cuotas_pendientes),
                        "suscripciones_restantes": float(subs_pendientes),
                        "compromisos_restantes": float(compr_maduros_pendientes),
                        "total": float(cierto_total),
                    },
                    "desglose_por_categoria": categorias,
                    "nivel_confianza": nivel_confianza,
                    "ciclos_analizados": cant_ciclos_datos,
                    "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
                    "advertencias": advertencias_sin_disp,
                    "datos_suficientes": False,
                    "mostrar_card": mostrar_card_moneda,
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
                    "mensaje": msg_no_disp,
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
            else:
                # Puerta cerrada con la API: solo periodo, certezas, calibración y mensaje; lo demás en null
                resultado[moneda.value.lower()] = {
                    "periodo": {
                        "fecha_inicio": inicio.isoformat(),
                        "fecha_fin": fin.isoformat(),
                        "dias_transcurridos": int(dias_transcurridos),
                        "dias_restantes": int(dias_restantes),
                        "dias_totales": int(dias_totales),
                    },
                    "gasto_proyectado_total": None,
                    "rango": None,
                    "rango_poco_informativo": None,
                    "balance_proyectado": None,
                    "ingresos_proyectados": None,
                    "certezas": {
                        "cuotas_restantes": float(cuotas_pendientes),
                        "suscripciones_restantes": float(subs_pendientes),
                        "compromisos_restantes": float(compr_maduros_pendientes),
                        "total": float(cierto_total),
                    },
                    "desglose_por_categoria": None,
                    "nivel_confianza": nivel_confianza,
                    "ciclos_analizados": cant_ciclos_datos,
                    "pesos": None,
                    "advertencias": advertencias_sin_disp,
                    "datos_suficientes": False,
                    "mostrar_card": mostrar_card_moneda,
                    "clasificacion": None,
                    "distribucion": None,
                    "intervalos": None,
                    "descomposicion": {
                        "cierto": float(cierto_total),
                        "recurrente_ya_ocurrido": float(recurrente_ya_ocurrido),
                        "variable_proyectado": None,
                    },
                    "mensaje_insuficiente": msg_no_disp,
                    "mensaje": msg_no_disp,
                    "calibracion": {
                        "pasa_puerta": False,
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
            msg_no_info = (
                "La proyección para este ciclo no es suficientemente informativa: el rango probable "
                "supera el 100% de tu gasto proyectado. Mostramos tus compromisos ciertos."
            )
            advertencias_salida = list(advertencias)
            if msg_no_info not in advertencias_salida:
                advertencias_salida.append(msg_no_info)

            # Puerta cerrada con la API: solo periodo, certezas, calibración y mensaje; lo demás en null
            resultado[moneda.value.lower()] = {
                "periodo": {
                    "fecha_inicio": inicio.isoformat(),
                    "fecha_fin": fin.isoformat(),
                    "dias_transcurridos": int(dias_transcurridos),
                    "dias_restantes": int(dias_restantes),
                    "dias_totales": int(dias_totales),
                },
                "gasto_proyectado_total": None,
                "rango": None,
                "rango_poco_informativo": True,
                "balance_proyectado": None,
                "ingresos_proyectados": None,
                "certezas": {
                    "cuotas_restantes": float(cuotas_pendientes),
                    "suscripciones_restantes": float(subs_pendientes),
                    "compromisos_restantes": float(compr_maduros_pendientes),
                    "total": float(cierto_total),
                },
                "desglose_por_categoria": None,
                "nivel_confianza": nivel_confianza,
                "ciclos_analizados": cant_ciclos_datos,
                "pesos": None,
                "advertencias": advertencias_salida,
                "datos_suficientes": False,
                "mostrar_card": mostrar_card_moneda,
                "clasificacion": None,
                "distribucion": None,
                "intervalos": None,
                "descomposicion": {
                    "cierto": float(cierto_total),
                    "recurrente_ya_ocurrido": float(recurrente_ya_ocurrido),
                    "variable_proyectado": None,
                },
                "mensaje_insuficiente": msg_no_info,
                "mensaje": msg_no_info,
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

        # Proyección de ingresos mediante ingreso esperado del ciclo
        ingreso_esp = calcular_ingreso_esperado_ciclo(
            db, usuario, inicio, fin, moneda, hoy=hoy, ctx=data["ctx"], txs_previa=data["txs"]
        )
        if ingreso_esp is None:
            ingreso_proy_val = None
            balance_proy_val = None
            msg_cobros = "Para calcular cómo terminás el ciclo necesitamos que cargues tus cobros."
            if msg_cobros not in advertencias:
                advertencias.append(msg_cobros)
        else:
            ingreso_proy_val = float(ingreso_esp)
            balance_proy_val = float(ingreso_esp - q50)

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
            "balance_proyectado": balance_proy_val,
            "ingresos_proyectados": ingreso_proy_val,
            "certezas": {
                "cuotas_restantes": float(cuotas_pendientes),
                "suscripciones_restantes": float(subs_pendientes),
                "compromisos_restantes": float(compr_maduros_pendientes),
                "total": float(cierto_total),
            },
            "desglose_por_categoria": categorias,
            "nivel_confianza": nivel_confianza,
            "ciclos_analizados": cant_ciclos_datos,
            "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
            "advertencias": advertencias,
            "datos_suficientes": True,
            "mostrar_card": mostrar_card_moneda,
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
            "mensaje": None,
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

    resultado["mostrar_card"] = bool(
        (resultado.get("ars") or {}).get("mostrar_card")
        or (resultado.get("usd") or {}).get("mostrar_card")
    )
    return resultado



def calcular_proyeccion(db: Session, usuario: Usuario) -> Dict[str, Any]:
    """
    Calcula la proyección financiera del usuario para el ciclo actual.
    Delega en el motor probabilístico calibrado.
    """
    return calcular_proyeccion_nueva(db, usuario)
