"""
Módulo de registro central de ejecuciones de tareas automáticas (APScheduler).
Envuelve jobs sync y async, persistiendo el resultado en la tabla ejecuciones_jobs
sin interrumpir la ejecución ni propagar errores de guardado.
Incluye el vigilante horario de tareas y el cálculo de estado.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta, date
import functools
import inspect
import time
from typing import Any, Callable, Optional
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import delete

from app.core.job_lock import _lock_thread_local, _lock_omitido_var
from app.models.ejecucion_job import EjecucionJob
from app.services.alerta_service import enviar_alerta_admin
from app.utils.fecha import hoy_argentina

logger = structlog.get_logger(__name__)

# Memoria de proceso para alertas de vigilante ya enviadas en el día (job_id -> fecha_argentina)
_alertas_vigilante_enviadas: dict[str, date] = {}


def _reset_alertas_vigilante() -> None:
    """Limpia el registro de alertas enviadas en memoria (utilizado en pruebas)."""
    _alertas_vigilante_enviadas.clear()


def _es_lock_omitido() -> bool:
    """Retorna True si durante la ejecución actual el lock fue rechazado."""
    if getattr(_lock_thread_local, "omitido", False):
        return True
    return bool(_lock_omitido_var.get())


def _limpiar_lock_omitido() -> None:
    """Restablece el indicador de lock omitido antes o después de ejecutar un job."""
    _lock_thread_local.omitido = False
    _lock_omitido_var.set(False)


def guardar_registro_ejecucion(
    job_id: str,
    inicio: datetime,
    fin: datetime,
    resultado: str,
    duracion_ms: int,
    detalle: Optional[str] = None,
    db: Optional[Session] = None,
) -> None:
    """
    Persiste una fila en ejecuciones_jobs.
    Si ocurre cualquier error en la base de datos, lo loguea y continúa sin relanzar.
    """
    from app.core.database import SessionLocal

    sesion_propia = False
    if db is None:
        db = SessionLocal()
        sesion_propia = True

    try:
        # Asegurar zona horaria UTC si viene sin tzinfo
        if inicio.tzinfo is None:
            inicio = inicio.replace(tzinfo=timezone.utc)
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=timezone.utc)

        registro = EjecucionJob(
            job_id=job_id,
            inicio=inicio,
            fin=fin,
            resultado=resultado,
            duracion_ms=duracion_ms,
            detalle=detalle,
        )
        db.add(registro)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(
            "Error al persistir registro de ejecución de job",
            job_id=job_id,
            error=str(e),
        )
    finally:
        if sesion_propia:
            db.close()


def registrar_ejecucion(job_id: str, funcion: Callable[..., Any]) -> Callable[..., Any]:
    """
    Envoltorio central para scheduler.add_job.
    - Anota inicio, corre la función y anota fin, duración y resultado.
    - Soporta funciones normales (sync) y async (corutinas).
    - Detecta si el lock fue omitido ('omitido').
    - Si la función lanza excepción, anota 'error' y relanza la excepción original.
    - Si falla el guardado del registro, se loguea y nunca rompe el job.
    """
    if inspect.iscoroutinefunction(funcion):
        @functools.wraps(funcion)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            _limpiar_lock_omitido()
            inicio = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            resultado = "ok"
            detalle: Optional[str] = None

            try:
                res = await funcion(*args, **kwargs)
                if _es_lock_omitido():
                    resultado = "omitido"
                return res
            except Exception as e:
                resultado = "error"
                detalle = f"{type(e).__name__}: {str(e)[:200]}"
                raise
            finally:
                t1 = time.perf_counter()
                fin = datetime.now(timezone.utc)
                duracion_ms = max(0, int((t1 - t0) * 1000))
                _limpiar_lock_omitido()
                try:
                    guardar_registro_ejecucion(
                        job_id=job_id,
                        inicio=inicio,
                        fin=fin,
                        resultado=resultado,
                        duracion_ms=duracion_ms,
                        detalle=detalle,
                    )
                except Exception as log_err:
                    logger.error(
                        "Error inesperado al guardar ejecución",
                        job_id=job_id,
                        error=str(log_err),
                    )

        return async_wrapper

    else:
        @functools.wraps(funcion)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _limpiar_lock_omitido()
            inicio = datetime.now(timezone.utc)
            t0 = time.perf_counter()
            resultado = "ok"
            detalle: Optional[str] = None

            try:
                res = funcion(*args, **kwargs)
                if _es_lock_omitido():
                    resultado = "omitido"
                return res
            except Exception as e:
                resultado = "error"
                detalle = f"{type(e).__name__}: {str(e)[:200]}"
                raise
            finally:
                t1 = time.perf_counter()
                fin = datetime.now(timezone.utc)
                duracion_ms = max(0, int((t1 - t0) * 1000))
                _limpiar_lock_omitido()
                try:
                    guardar_registro_ejecucion(
                        job_id=job_id,
                        inicio=inicio,
                        fin=fin,
                        resultado=resultado,
                        duracion_ms=duracion_ms,
                        detalle=detalle,
                    )
                except Exception as log_err:
                    logger.error(
                        "Error inesperado al guardar ejecución",
                        job_id=job_id,
                        error=str(log_err),
                    )

        return sync_wrapper


def calcular_estado_job(
    job_id: str,
    periodo_esperado_minutos: int,
    db: Session,
    ahora: Optional[datetime] = None,
) -> str:
    """
    Determina el estado actual de un job según las reglas del paso:
    - 'Sin datos' si nunca corrió desde que existe el registro.
    - 'Falló' si la última corrida fue 'error'.
    - 'Atrasado' si la última corrida con resultado 'ok' tiene más de 2 × periodo_esperado_minutos (o si no tiene ningún 'ok').
    - 'Al día' en cualquier otro caso.
    """
    if ahora is None:
        ahora = datetime.now(timezone.utc)
    elif ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=timezone.utc)

    # 1. Buscar la última corrida absoluta
    ultima = (
        db.query(EjecucionJob)
        .filter(EjecucionJob.job_id == job_id)
        .order_by(EjecucionJob.inicio.desc())
        .first()
    )

    if not ultima:
        return "Sin datos"

    if ultima.resultado == "error":
        return "Falló"

    # 2. Buscar la última corrida exitosa con resultado 'ok'
    ultimo_ok = (
        db.query(EjecucionJob)
        .filter(EjecucionJob.job_id == job_id, EjecucionJob.resultado == "ok")
        .order_by(EjecucionJob.inicio.desc())
        .first()
    )

    if not ultimo_ok:
        return "Atrasado"

    inicio_ok = ultimo_ok.inicio
    if inicio_ok.tzinfo is None:
        inicio_ok = inicio_ok.replace(tzinfo=timezone.utc)

    tiempo_transcurrido = ahora - inicio_ok
    if tiempo_transcurrido > timedelta(minutes=2 * periodo_esperado_minutos):
        return "Atrasado"

    return "Al día"


def vigilar_tareas(db: Optional[Session] = None) -> None:
    """
    Tarea vigilante ejecutada periódicamente (cada 60 minutos).
    - Para cada job en 'Atrasado' o 'Falló', envía enviar_alerta_admin una sola vez
      por job y por día de Argentina (memoria en el proceso).
    - Asunto: '[Argentum] Tarea automática: <nombre> <atrasada|falló>'
    - Cuerpo con los datos de la última corrida.
    - Ignora los jobs 'Sin datos'.
    - Borra las filas de ejecuciones_jobs con más de 14 días de antigüedad.
    """
    from app.core.database import SessionLocal
    from app.core.jobs_catalogo import JOBS_CATALOGO

    sesion_propia = False
    if db is None:
        db = SessionLocal()
        sesion_propia = True

    try:
        hoy = hoy_argentina()
        ahora = datetime.now(timezone.utc)

        # 1. Monitoreo de tareas
        for item in JOBS_CATALOGO:
            estado = calcular_estado_job(
                job_id=item.job_id,
                periodo_esperado_minutos=item.periodo_esperado_minutos,
                db=db,
                ahora=ahora,
            )

            # Ignora los jobs "Sin datos" y los que están "Al día"
            if estado in ("Atrasado", "Falló"):
                if _alertas_vigilante_enviadas.get(item.job_id) == hoy:
                    continue

                tipo_estado = "atrasada" if estado == "Atrasado" else "falló"
                asunto = f"[Argentum] Tarea automática: {item.nombre} {tipo_estado}"

                ultima = (
                    db.query(EjecucionJob)
                    .filter(EjecucionJob.job_id == item.job_id)
                    .order_by(EjecucionJob.inicio.desc())
                    .first()
                )

                if ultima:
                    cuerpo = (
                        f"La tarea automática '{item.nombre}' ({item.job_id}) se encuentra en estado: {estado}.\n\n"
                        f"Detalle de la última corrida registrada:\n"
                        f"- Inicio: {ultima.inicio}\n"
                        f"- Fin: {ultima.fin}\n"
                        f"- Resultado: {ultima.resultado}\n"
                        f"- Duración: {ultima.duracion_ms} ms\n"
                        f"- Detalle: {ultima.detalle or 'Sin observaciones'}\n"
                    )
                else:
                    cuerpo = (
                        f"La tarea automática '{item.nombre}' ({item.job_id}) se encuentra en estado: {estado} "
                        f"sin corridas previas registradas."
                    )

                enviar_alerta_admin(
                    asunto=asunto,
                    cuerpo=cuerpo,
                    clave=f"vigilante:{item.job_id}:{hoy}",
                )
                _alertas_vigilante_enviadas[item.job_id] = hoy

        # 2. Purgar ejecuciones de más de 14 días
        limite_14d = ahora - timedelta(days=14)
        db.execute(
            delete(EjecucionJob).where(EjecucionJob.inicio < limite_14d)
        )
        db.commit()

    except Exception as e:
        db.rollback()
        logger.exception("Error en ejecución de vigilar_tareas", error=str(e))
    finally:
        if sesion_propia:
            db.close()
