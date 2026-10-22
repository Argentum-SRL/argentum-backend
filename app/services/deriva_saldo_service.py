"""
Servicio para vigilar la deriva entre el saldo guardado y el saldo calculado de las billeteras.
Ubicación: app/services/deriva_saldo_service.py
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
import structlog

from app.core.database import SessionLocal
from app.core.job_lock import intentar_tomar_lock_job, liberar_lock_job
from app.models.billetera import Billetera
from app.services import alerta_service
from app.services.conciliacion_service import calcular_saldo_teorico
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger(__name__)


def contar_billeteras_con_diferencia(db: Session, hoy: Optional[date] = None) -> int:
    """
    Compara el saldo_actual de todas las billeteras con su saldo teórico a la fecha `hoy`
    (o hoy_argentina() por defecto) y retorna la cantidad de billeteras con diferencia != 0.
    """
    fecha_corte = hoy if hoy is not None else hoy_argentina()
    billeteras = db.execute(select(Billetera.id, Billetera.saldo_actual)).all()

    conteo = 0
    for b_id, saldo_act in billeteras:
        s_act = saldo_act if saldo_act is not None else Decimal("0.00")
        s_teo = calcular_saldo_teorico(db, b_id, hasta=fecha_corte)
        if s_act - s_teo != Decimal("0.00"):
            conteo += 1

    return conteo


def _job_vigilar_deriva_saldos(db: Optional[Session] = None) -> int:
    """
    Job nocturno que vigila la consistencia de los saldos guardados frente a los calculados.
    Utiliza advisory lock para coordinar instancias y asegurar ejecución única.
    """
    sesion_propia = False
    if db is None:
        db = SessionLocal()
        sesion_propia = True

    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_vigilar_deriva_saldos"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_vigilar_deriva_saldos",
            )
            return 0
        lock_adquirido = True

        conteo = contar_billeteras_con_diferencia(db)
        if conteo > 0:
            if conteo == 1:
                cant_str = "1 billetera con diferencia."
            else:
                cant_str = f"{conteo} billeteras con diferencia."
            cuerpo = f"{cant_str}\nRevisalo con el verificador. Este aviso no incluye datos de usuarios."
            alerta_service.enviar_alerta_admin(
                asunto="[Argentum] Saldos con diferencia",
                cuerpo=cuerpo,
                clave="deriva_saldos",
            )
        return conteo
    except Exception as e:
        logger.exception("Error en job _job_vigilar_deriva_saldos: %s", e)
        if sesion_propia:
            db.rollback()
        raise
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_vigilar_deriva_saldos")
        if sesion_propia:
            db.close()
