"""
Módulo de locks distribuidos mediante PostgreSQL Advisory Locks.
Permite coordinar tareas programadas (APScheduler) entre múltiples procesos o réplicas
del backend para garantizar que cada job se ejecute en una sola instancia a la vez.
"""
from sqlalchemy.orm import Session
from sqlalchemy import text
import structlog

logger = structlog.get_logger(__name__)


def intentar_tomar_lock_job(db: Session, nombre_job: str) -> bool:
    """
    Intenta adquirir un advisory lock exclusivo a nivel de sesión en Postgres
    usando el hash del nombre del job.
    
    Devuelve True si consiguió el lock (este proceso debe correr el job),
    False si no (otro proceso ya lo está corriendo ahora mismo).
    """
    try:
        resultado = db.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:nombre))"),
            {"nombre": nombre_job}
        ).scalar()
        return bool(resultado)
    except Exception as e:
        logger.error(
            "Error al intentar tomar advisory lock para job",
            job=nombre_job,
            error=str(e),
        )
        return False


def liberar_lock_job(db: Session, nombre_job: str) -> None:
    """
    Libera el advisory lock previamente adquirido en Postgres para el job.
    """
    try:
        db.execute(
            text("SELECT pg_advisory_unlock(hashtext(:nombre))"),
            {"nombre": nombre_job}
        )
    except Exception as e:
        logger.error(
            "Error al liberar advisory lock para job",
            job=nombre_job,
            error=str(e),
        )
