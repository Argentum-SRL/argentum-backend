from __future__ import annotations

import logging
from datetime import timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.factura import Factura
from app.models.notificacion import NivelNotificacion, TipoNotificacion
from app.services.notificacion_scheduler_service import intentar_tomar_lock_job, liberar_lock_job
from app.services.notificacion_service import crear_notificacion
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)


def _job_notificaciones_facturas(db_session_factory):
    """
    Tarea programada diaria: notifica vencimientos de facturas pendientes a las 07:30 UTC.
    """
    db: Session = db_session_factory()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_notificaciones_facturas"):
            logger.info("Job omitido: ya se está ejecutando en otra instancia (_job_notificaciones_facturas)")
            return
        lock_adquirido = True
        hoy = hoy_argentina()

        # 1. Facturas pendientes que vencen en hoy + 3 días
        vence_en_3 = hoy + timedelta(days=3)
        stmt_3 = select(Factura).where(
            Factura.estado == "pendiente",
            Factura.fecha_vencimiento == vence_en_3,
        )
        facturas_3 = db.execute(stmt_3).scalars().all()
        for f in facturas_3:
            monto_fmt = formatear_monto(f.monto, f.moneda)
            dd_mm = f"{f.fecha_vencimiento.day:02d}/{f.fecha_vencimiento.month:02d}"
            mensaje = f"La factura de {f.descripcion} por {monto_fmt} vence en 3 días ({dd_mm})."
            grupo = f"FACTURA_VENCE_{f.id}_{hoy:%Y%m%d}"
            crear_notificacion(
                db=db,
                usuario_id=f.usuario_id,
                tipo=TipoNotificacion.FACTURA_VENCE,
                nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                mensaje=mensaje,
                entidad_tipo="factura",
                entidad_id=f.id,
                deep_link="/app/dashboard",
                canal_web=True,
                canal_whatsapp=False,
                canal_email=False,
                grupo_agrupacion_override=grupo,
                commit=True,
            )

        # 2. Facturas pendientes que vencen hoy
        stmt_hoy = select(Factura).where(
            Factura.estado == "pendiente",
            Factura.fecha_vencimiento == hoy,
        )
        facturas_hoy = db.execute(stmt_hoy).scalars().all()
        for f in facturas_hoy:
            monto_fmt = formatear_monto(f.monto, f.moneda)
            mensaje = f"La factura de {f.descripcion} por {monto_fmt} vence hoy."
            grupo = f"FACTURA_VENCE_{f.id}_{hoy:%Y%m%d}"
            crear_notificacion(
                db=db,
                usuario_id=f.usuario_id,
                tipo=TipoNotificacion.FACTURA_VENCE,
                nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                mensaje=mensaje,
                entidad_tipo="factura",
                entidad_id=f.id,
                deep_link="/app/dashboard",
                canal_web=True,
                canal_whatsapp=False,
                canal_email=False,
                grupo_agrupacion_override=grupo,
                commit=True,
            )

        logger.info("Job notificaciones_facturas completado")
    except Exception:
        logger.exception("Error en _job_notificaciones_facturas")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_notificaciones_facturas")
        db.close()
