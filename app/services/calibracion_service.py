from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.calibracion_usuario import CalibracionUsuario
from app.models.usuario import EstadoUsuario, Moneda, Usuario
from app.services.datos_motor_service import cargar_datos_motor, ciclos_anteriores
from app.services.definiciones_service import es_gasto
from app.utils.finanzas import (
    MINIMO_CICLOS_EVALUABLES_CALIBRACION,
    NIVEL_EVALUADO_PUERTA,
    UMBRAL_ANCHO_MAXIMO_RELATIVO_80,
    UMBRAL_COBERTURA_MINIMA_80,
    ZERO,
    clasificar_gastos,
    estimar_gasto_diario_basico_robusto,
    evaluar_puerta_calibracion,
    gasto_ciclo,
    mad,
    student_t_critical,
    weighted_quantile,
)

from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)

_locks_calibracion: dict[str, threading.Lock] = {}
_master_lock = threading.Lock()


def _obtener_lock_usuario(usuario_id_str: str) -> threading.Lock:
    with _master_lock:
        if usuario_id_str not in _locks_calibracion:
            _locks_calibracion[usuario_id_str] = threading.Lock()
        return _locks_calibracion[usuario_id_str]


def calcular_calibracion_usuario(
    db: Session,
    usuario_id: UUID | str | Usuario,
    moneda: Moneda | str = Moneda.ARS,
) -> dict[str, Any] | None:
    """Calcula la calibración para un usuario y moneda sin realizar escrituras en la base de datos."""
    t0 = time.perf_counter()
    if isinstance(usuario_id, Usuario):
        usuario = usuario_id
    elif isinstance(usuario_id, str):
        usuario = db.query(Usuario).filter(Usuario.id == usuario_id).first()
    else:
        usuario = db.get(Usuario, usuario_id)

    if not usuario:
        logger.warning(f"Usuario {usuario_id} no encontrado para calibración.")
        return None

    if isinstance(moneda, str):
        moneda = Moneda(moneda)

    hoy = hoy_argentina()
    inicio_actual, fin_actual = get_ciclo_fechas(usuario, hoy)

    data = cargar_datos_motor(db, usuario)
    anteriores = ciclos_anteriores(usuario, hoy, 12)
    ciclos_con_datos = [
        (c_ini, c_fin)
        for c_ini, c_fin in anteriores
        if any(c_ini <= tx.fecha <= c_fin and tx.moneda == moneda for tx in data["txs"])
    ]
    cerrados = [c for c in ciclos_con_datos if c[1] < hoy]

    if len(cerrados) < MINIMO_CICLOS_EVALUABLES_CALIBRACION:
        duracion_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "pasa_puerta": False,
            "motivo": "pocos_ciclos",
            "mensaje": (
                f"Tu historial cuenta con {len(cerrados)} ciclos evaluables (se requieren al menos "
                f"{MINIMO_CICLOS_EVALUABLES_CALIBRACION} ciclos cerrados con proyección). Mostramos tus "
                f"compromisos ciertos (cuotas y suscripciones). La proyección probabilística se activará "
                f"cuando acumules suficiente historia para validar su calibración."
            ),
            "ciclos_evaluados": len(cerrados),
            "cobertura_50": None,
            "cobertura_80": None,
            "cobertura_95": None,
            "ancho_medio_80_rel": None,
            "detalles": [],
            "duracion_ms": duracion_ms,
            "sin_fila": True,
            "clasificacion": None,
        }

    comprometidos_externos = [
        *(cuota for cuota, _ in data["cuotas"] if not cuota.pagada and cuota.fecha_vencimiento >= hoy),
        *data["suscripciones"],
    ]
    ctx = data["ctx"]

    clasificacion = clasificar_gastos(data["txs"], anteriores, data["ipc"], hoy, comprometidos_externos, ctx=ctx)
    compromiso_tx_ids = {
        tx_id for s in clasificacion.streams if s.clase == "COMPROMISO" for tx_id in s.transacciones_ids
    }
    compromiso_tx_ids.update(
        tx.id
        for tx in data["txs"]
        if getattr(tx, "suscripcion_id", None) is not None
    )
    calib = evaluar_calibracion_usuario(data, usuario, moneda, compromiso_tx_ids)

    duracion_ms = (time.perf_counter() - t0) * 1000.0
    moneda_str = moneda.value if hasattr(moneda, "value") else str(moneda)

    cob_50 = Decimal(str(round(calib["cobertura_50"], 4))) if calib.get("cobertura_50") is not None else None
    cob_80 = Decimal(str(round(calib["cobertura_80"], 4))) if calib.get("cobertura_80") is not None else None
    cob_95 = Decimal(str(round(calib["cobertura_95"], 4))) if calib.get("cobertura_95") is not None else None
    ancho_rel = Decimal(str(round(calib["ancho_medio_80_rel"], 4))) if calib.get("ancho_medio_80_rel") is not None else None
    dur_dec = Decimal(str(round(duracion_ms, 2)))

    import json
    detalles_json = json.loads(json.dumps(calib.get("detalles"), default=float)) if calib.get("detalles") is not None else None

    return {
        "usuario_id": usuario.id,
        "moneda": moneda_str,
        "inicio_ciclo": inicio_actual,
        "pasa_puerta": calib["pasa_puerta"],
        "motivo": calib.get("motivo"),
        "mensaje": calib.get("mensaje"),
        "ciclos_evaluados": calib.get("ciclos_evaluados", 0),
        "cobertura_50": cob_50,
        "cobertura_80": cob_80,
        "cobertura_95": cob_95,
        "ancho_medio_80_rel": ancho_rel,
        "detalles": detalles_json,
        "duracion_ms": dur_dec,
        "clasificacion": clasificacion,
    }


def calcular_y_guardar_calibracion_usuario(
    db: Session,
    usuario_id: UUID | str,
    moneda: Moneda | str = Moneda.ARS,
) -> CalibracionUsuario | dict[str, Any] | None:
    """Calcula la calibración y guarda el resultado en la tabla calibraciones_usuario.

    Delega el cálculo puro en calcular_calibracion_usuario y preserva la misma firma y comportamiento.
    """
    res = calcular_calibracion_usuario(db, usuario_id, moneda)
    if not res:
        return None
    if res.get("sin_fila"):
        return res

    moneda_str = res["moneda"]
    fila = (
        db.query(CalibracionUsuario)
        .filter(
            CalibracionUsuario.usuario_id == res["usuario_id"],
            CalibracionUsuario.moneda == moneda_str,
        )
        .first()
    )

    if not fila:
        fila = CalibracionUsuario(
            usuario_id=res["usuario_id"],
            moneda=moneda_str,
            inicio_ciclo=res["inicio_ciclo"],
            pasa_puerta=res["pasa_puerta"],
            motivo=res["motivo"],
            mensaje=res["mensaje"],
            ciclos_evaluados=res["ciclos_evaluados"],
            cobertura_50=res["cobertura_50"],
            cobertura_80=res["cobertura_80"],
            cobertura_95=res["cobertura_95"],
            ancho_medio_80_rel=res["ancho_medio_80_rel"],
            detalles=res["detalles"],
            fecha_calculo=datetime.now(timezone.utc),
            duracion_ms=res["duracion_ms"],
        )
        db.add(fila)
    else:
        fila.inicio_ciclo = res["inicio_ciclo"]
        fila.pasa_puerta = res["pasa_puerta"]
        fila.motivo = res["motivo"]
        fila.mensaje = res["mensaje"]
        fila.ciclos_evaluados = res["ciclos_evaluados"]
        fila.cobertura_50 = res["cobertura_50"]
        fila.cobertura_80 = res["cobertura_80"]
        fila.cobertura_95 = res["cobertura_95"]
        fila.ancho_medio_80_rel = res["ancho_medio_80_rel"]
        fila.detalles = res["detalles"]
        fila.fecha_calculo = datetime.now(timezone.utc)
        fila.duracion_ms = res["duracion_ms"]

    db.commit()
    db.refresh(fila)
    return fila


def recalcular_calibraciones_todos(session_factory) -> int:
    """Recorre todos los usuarios activos y recalcula su calibración. Si un usuario falla, loguea y sigue."""
    db = session_factory()
    try:
        usuarios = db.execute(
            select(Usuario.id).where(Usuario.estado == EstadoUsuario.ACTIVO)
        ).scalars().all()
    finally:
        db.close()

    total_calculados = 0
    for uid in usuarios:
        db_user = session_factory()
        try:
            for moneda in (Moneda.ARS, Moneda.USD):
                calcular_y_guardar_calibracion_usuario(db_user, uid, moneda)
                total_calculados += 1
        except Exception as e:
            logger.error(f"Error recalculando calibración para usuario {uid}: {e}", exc_info=True)
        finally:
            db_user.close()

    logger.info(f"Job nocturno: recalibradas {total_calculados} combinaciones usuario-moneda.")
    return total_calculados


def _tarea_background_calibracion(usuario_id: Any):
    """Tarea ejecutada en BackgroundTasks con su propia sesión y lock por usuario."""
    u_id_str = str(usuario_id)
    lock = _obtener_lock_usuario(u_id_str)
    adquirido = lock.acquire(blocking=False)
    if not adquirido:
        logger.info(f"Cálculo de calibración en background omitido: ya en ejecución para {u_id_str}")
        return
    try:
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            calcular_y_guardar_calibracion_usuario(db, usuario_id, Moneda.ARS)
            calcular_y_guardar_calibracion_usuario(db, usuario_id, Moneda.USD)
        except Exception as e:
            logger.error(f"Error en calibración a demanda para usuario {u_id_str}: {e}", exc_info=True)
        finally:
            db.close()
    finally:
        lock.release()


def disparar_calibracion_a_demanda(background_tasks: BackgroundTasks, usuario_id: Any) -> None:
    """Programa el cálculo de calibración en segundo plano para un usuario."""
    background_tasks.add_task(_tarea_background_calibracion, usuario_id)


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
    anteriores = ciclos_anteriores(usuario, hoy, 12)
    cerrados = [c for c in anteriores if c[1] < hoy]

    compr_ids = set(compromiso_tx_ids or set())
    resultados_ciclos: list[dict[str, Any]] = []
    ctx = data["ctx"]

    for inicio_k, fin_k in cerrados:
        fecha_corte_k = inicio_k - timedelta(days=1)
        txs_previas = [tx for tx in data["txs"] if tx.fecha <= fecha_corte_k and tx.moneda == moneda]

        # Consumo real del ciclo k
        txs_k = [tx for tx in data["txs"] if inicio_k <= tx.fecha <= fin_k and tx.moneda == moneda and es_gasto(tx, ctx)]
        gc = gasto_ciclo(txs_k, inicio_k, fin_k, fin_k, data["ipc"], moneda, ctx=ctx)
        y_real = gc.deflactado
        if y_real <= ZERO:
            continue

        anteriores_k = ciclos_anteriores(usuario, inicio_k, 12)
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
        clasif_k = clasificar_gastos(txs_previas, anteriores_k, data["ipc"], inicio_k, comprometidos_ext, ctx=ctx)
        compr_ids_k = {tx_id for s in clasif_k.streams if s.clase == "COMPROMISO" for tx_id in s.transacciones_ids}
        compr_ids_k.update(
            tx.id for tx in txs_previas
            if getattr(tx, "suscripcion_id", None) is not None
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
            txs_c = [tx for tx in txs_previas if cp_ini <= tx.fecha <= cp_fin and es_gasto(tx, ctx)]
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

