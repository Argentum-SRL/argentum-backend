"""
Módulo de locks distribuidos mediante PostgreSQL Advisory Locks.
Permite coordinar tareas programadas (APScheduler) entre múltiples procesos o réplicas
del backend para garantizar que cada job se ejecute en una sola instancia a la vez.
"""
from __future__ import annotations

import structlog
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.core.database import engine

logger = structlog.get_logger(__name__)

# Conexiones dedicadas retenidas para cada job con lock activo
_conexiones_lock: dict[str, Connection] = {}


def intentar_tomar_lock_job(db: Session, nombre_job: str) -> bool:
    """
    Intenta adquirir un advisory lock exclusivo en Postgres usando el hash del nombre del job.
    Usa una conexión propia del engine para aislar el lock del ciclo de vida de la Session.
    Devuelve True si consiguió el lock, False si no.
    """
    if engine.dialect.name != "postgresql":
        return True

    conn: Connection | None = None
    try:
        conn = engine.connect()
        resultado = conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:nombre))"),
            {"nombre": nombre_job},
        ).scalar()
        if bool(resultado):
            _conexiones_lock[nombre_job] = conn
            return True
        else:
            conn.close()
            return False
    except Exception as e:
        logger.error(
            "Error al intentar tomar advisory lock para job",
            job=nombre_job,
            error=str(e),
        )
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return False


def liberar_lock_job(db: Session, nombre_job: str) -> None:
    """
    Libera el advisory lock previamente adquirido en Postgres para el job,
    usando la misma conexión con la que se adquirió, realiza commit y cierra la conexión.
    """
    if engine.dialect.name != "postgresql":
        return

    conn = _conexiones_lock.pop(nombre_job, None)
    if conn is None:
        return

    try:
        conn.execute(
            text("SELECT pg_advisory_unlock(hashtext(:nombre))"),
            {"nombre": nombre_job},
        )
        conn.commit()
    except Exception as e:
        logger.error(
            "Error al liberar advisory lock para job",
            job=nombre_job,
            error=str(e),
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass
