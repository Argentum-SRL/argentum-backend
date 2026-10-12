"""
Servicio para la aplicación automática de movimientos con fecha llegada.
"""
from __future__ import annotations

import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
import structlog

from app.core.database import SessionLocal
from app.core.job_lock import intentar_tomar_lock_job, liberar_lock_job
from app.models.billetera import Billetera
from app.models.notificacion import TipoNotificacion, NivelNotificacion
from app.models.transaccion import Transaccion, TipoTransaccion, OrigenTransaccion
from app.services.evento_service import emitir_evento_actualizacion
from app.services.notificacion_service import (
    obtener_configuracion,
    resolver_canales_notificacion,
    crear_notificacion,
)
from app.services.transaccion_service import _afecta_saldo, _validar_moneda_coincide

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger(__name__)


def aplicar_movimientos_con_fecha_llegada(db: Session) -> int:
    """
    Toma las transacciones con saldo_aplicado=False para las que _afecta_saldo da True,
    mueve el saldo igual que crear_transaccion, marca saldo_aplicado=True,
    hace un solo commit y llama a emitir_evento_actualizacion por usuario.
    Es idempotente.
    """
    pendientes = db.execute(
        select(Transaccion)
        .where(Transaccion.saldo_aplicado.is_(False))
        .order_by(Transaccion.fecha, Transaccion.fecha_creacion)
    ).scalars().all()

    aplicados_count = 0
    usuarios_afectados = set()

    for tx in pendientes:
        if not _afecta_saldo(tx):
            continue

        billetera = db.get(Billetera, tx.billetera_id)
        if not billetera:
            logger.warning(
                "Billetera no encontrada para transacción pendiente de saldo",
                extra={"transaccion_id": str(tx.id), "billetera_id": str(tx.billetera_id)},
            )
            continue

        _validar_moneda_coincide(tx.moneda, billetera)

        # Mover saldo igual que crear_transaccion
        if tx.tipo == TipoTransaccion.INGRESO:
            billetera.saldo_actual += tx.monto
        else:
            billetera.saldo_actual -= tx.monto
            if billetera.saldo_actual <= 0:
                try:
                    config = obtener_configuracion(db, tx.usuario_id, commit=False)
                    canales = resolver_canales_notificacion(config, TipoNotificacion.SALDO_CERO)
                    if canales is not None:
                        canal_web, canal_whatsapp = canales
                        if tx.origen == OrigenTransaccion.IA_WPP:
                            canal_whatsapp = False
                        crear_notificacion(
                            db=db,
                            usuario_id=tx.usuario_id,
                            tipo=TipoNotificacion.SALDO_CERO,
                            nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                            mensaje=f"Tu billetera '{billetera.nombre}' quedó sin saldo disponible.",
                            entidad_tipo="billetera",
                            entidad_id=billetera.id,
                            deep_link="/app/billeteras",
                            canal_web=canal_web,
                            canal_whatsapp=canal_whatsapp,
                            commit=False,
                            datos_template={"billetera_nombre": billetera.nombre},
                        )
                except Exception:
                    pass

        tx.saldo_aplicado = True
        aplicados_count += 1
        usuarios_afectados.add(tx.usuario_id)

    if aplicados_count > 0:
        for u_id in usuarios_afectados:
            emitir_evento_actualizacion(db, u_id, "billeteras")
            emitir_evento_actualizacion(db, u_id, "transacciones")

        db.commit()
        logger.info(
            "Se aplicaron %d transacciones programadas para %d usuarios.",
            aplicados_count,
            len(usuarios_afectados),
        )

    return aplicados_count


def _job_aplicar_movimientos_con_fecha_llegada(db: Optional[Session] = None) -> int:
    """
    Job que corre todos los días a las 00:05 de Argentina.
    Utiliza advisory lock para coordinar instancias y asegurar idempotencia.
    """
    sesion_propia = False
    if db is None:
        db = SessionLocal()
        sesion_propia = True

    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_aplicar_movimientos_con_fecha_llegada"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_aplicar_movimientos_con_fecha_llegada",
            )
            return 0
        lock_adquirido = True

        return aplicar_movimientos_con_fecha_llegada(db)
    except Exception as e:
        logger.exception("Error en job _job_aplicar_movimientos_con_fecha_llegada: %s", e)
        if sesion_propia:
            db.rollback()
        raise
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_aplicar_movimientos_con_fecha_llegada")
        if sesion_propia:
            db.close()
