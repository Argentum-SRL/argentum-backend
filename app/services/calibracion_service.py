from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.calibracion_usuario import CalibracionUsuario
from app.models.usuario import EstadoUsuario, Moneda, Usuario
from app.services.analisis_financiero_service import (
    _carga,
    _ciclos_anteriores,
    clasificar_gastos,
    evaluar_calibracion_usuario,
)
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina
from app.utils.finanzas import MINIMO_CICLOS_EVALUABLES_CALIBRACION

logger = logging.getLogger(__name__)

_locks_calibracion: dict[str, threading.Lock] = {}
_master_lock = threading.Lock()


def _obtener_lock_usuario(usuario_id_str: str) -> threading.Lock:
    with _master_lock:
        if usuario_id_str not in _locks_calibracion:
            _locks_calibracion[usuario_id_str] = threading.Lock()
        return _locks_calibracion[usuario_id_str]


def calcular_y_guardar_calibracion_usuario(
    db: Session,
    usuario_id: UUID | str,
    moneda: Moneda | str = Moneda.ARS,
) -> CalibracionUsuario | None:
    """Calcula la calibración para un usuario y moneda usando evaluar_calibracion_usuario sin cambios y la guarda.

    Reemplaza la fila anterior (upsert por usuario_id y moneda).
    Si el usuario no tiene ciclos cerrados suficientes (< 6), guarda o responde pocos_ciclos directamente.
    """
    t0 = time.perf_counter()
    if isinstance(usuario_id, str):
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

    data = _carga(db, usuario)
    anteriores = _ciclos_anteriores(usuario, hoy, 12)
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
        }

    comprometidos_externos = [
        *(cuota for cuota, _ in data["cuotas"] if not cuota.pagada and cuota.fecha_vencimiento >= hoy),
        *data["suscripciones"],
    ]
    clasificacion = clasificar_gastos(data["txs"], anteriores, data["ipc"], hoy, comprometidos_externos)
    compromiso_tx_ids = {
        tx_id for s in clasificacion.streams if s.clase == "COMPROMISO" for tx_id in s.transacciones_ids
    }
    compromiso_tx_ids.update(
        tx.id
        for tx in data["txs"]
        if getattr(tx, "es_recurrente", False) or getattr(tx, "recurrente_id", None) is not None
    )
    calib = evaluar_calibracion_usuario(data, usuario, moneda, compromiso_tx_ids)

    duracion_ms = (time.perf_counter() - t0) * 1000.0
    moneda_str = moneda.value if hasattr(moneda, "value") else str(moneda)

    fila = (
        db.query(CalibracionUsuario)
        .filter(
            CalibracionUsuario.usuario_id == usuario.id,
            CalibracionUsuario.moneda == moneda_str,
        )
        .first()
    )

    cob_50 = Decimal(str(round(calib["cobertura_50"], 4))) if calib.get("cobertura_50") is not None else None
    cob_80 = Decimal(str(round(calib["cobertura_80"], 4))) if calib.get("cobertura_80") is not None else None
    cob_95 = Decimal(str(round(calib["cobertura_95"], 4))) if calib.get("cobertura_95") is not None else None
    ancho_rel = Decimal(str(round(calib["ancho_medio_80_rel"], 4))) if calib.get("ancho_medio_80_rel") is not None else None
    dur_dec = Decimal(str(round(duracion_ms, 2)))

    import json
    detalles_json = json.loads(json.dumps(calib.get("detalles"), default=float)) if calib.get("detalles") is not None else None

    if not fila:
        fila = CalibracionUsuario(
            usuario_id=usuario.id,
            moneda=moneda_str,
            inicio_ciclo=inicio_actual,
            pasa_puerta=calib["pasa_puerta"],
            motivo=calib.get("motivo"),
            mensaje=calib.get("mensaje"),
            ciclos_evaluados=calib.get("ciclos_evaluados", 0),
            cobertura_50=cob_50,
            cobertura_80=cob_80,
            cobertura_95=cob_95,
            ancho_medio_80_rel=ancho_rel,
            detalles=detalles_json,
            fecha_calculo=datetime.now(timezone.utc),
            duracion_ms=dur_dec,
        )
        db.add(fila)
    else:
        fila.inicio_ciclo = inicio_actual
        fila.pasa_puerta = calib["pasa_puerta"]
        fila.motivo = calib.get("motivo")
        fila.mensaje = calib.get("mensaje")
        fila.ciclos_evaluados = calib.get("ciclos_evaluados", 0)
        fila.cobertura_50 = cob_50
        fila.cobertura_80 = cob_80
        fila.cobertura_95 = cob_95
        fila.ancho_medio_80_rel = ancho_rel
        fila.detalles = detalles_json
        fila.fecha_calculo = datetime.now(timezone.utc)
        fila.duracion_ms = dur_dec

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
