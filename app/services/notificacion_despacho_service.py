"""
Servicio de despacho y reintento de notificaciones inmediatas por WhatsApp.
Maneja el envío de avisos de seguridad (cambio de contraseña y cambio de email)
con reintentos acotados y alerta administrativa ante fallos persistentes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from app.core.job_lock import intentar_tomar_lock_job, liberar_lock_job
from app.core.politica_notificaciones import (
    POLITICA_WHATSAPP,
    VENTANA_ALERTA_ADMIN_MAX,
    VENTANA_ALERTA_ADMIN_MIN,
    VENTANA_REINTENTO_INMEDIATA_MAX,
    VENTANA_REINTENTO_INMEDIATA_MIN,
    componentes,
    plantilla_y_valores,
    puede_salir_por_whatsapp,
)
from app.models.notificacion import (
    MENSAJE_CAMBIO_CONTRASENA_WPP,
    Notificacion,
    TipoNotificacion,
)
from app.models.usuario import Usuario
from app.services.alerta_service import enviar_alerta_admin
from app.services.whatsapp_service import enviar_whatsapp, enviar_whatsapp_template
from app.utils.fecha import ahora_argentina

logger = structlog.get_logger(__name__)


def despachar_inmediata(db: Session, notif_id: UUID | Any) -> bool:
    """
    Despacha inmediatamente un aviso de seguridad por WhatsApp.
    Aplica bloqueo de fila (with_for_update skip_locked) para prevenir envíos duplicados.
    Nunca lanza excepciones hacia el llamador.
    """
    try:
        notif = (
            db.query(Notificacion)
            .filter(Notificacion.id == notif_id)
            .with_for_update(skip_locked=True)
            .first()
        )
        if not notif:
            return False
        if notif.enviada_whatsapp:
            return False
        if POLITICA_WHATSAPP.get(notif.tipo) != "inmediata":
            return False
        if not puede_salir_por_whatsapp(notif):
            return False

        usuario = db.query(Usuario).filter(Usuario.id == notif.usuario_id).first()
        if not usuario or not usuario.telefono:
            return False

        pv = plantilla_y_valores(notif)
        if not pv:
            return False
        plantilla, valores = pv

        enviado = enviar_whatsapp_template(
            usuario.telefono,
            plantilla,
            "es",
            componentes(valores),
            max_intentos=1,
        )
        if not enviado and notif.tipo == TipoNotificacion.CAMBIO_CONTRASENA:
            enviado = enviar_whatsapp(usuario.telefono, MENSAJE_CAMBIO_CONTRASENA_WPP)

        if enviado:
            notif.enviada_whatsapp = True
            db.commit()
            return True
        return False
    except Exception as e:
        logger.error(
            "Error al despachar notificación inmediata por WhatsApp",
            notif_id=str(notif_id),
            error=str(e),
            exc_info=True,
        )
        return False


def _calcular_edad(ahora: datetime, fecha_creacion: datetime):
    """Calcula la diferencia temporal contemplando zonas horarias."""
    if fecha_creacion.tzinfo is not None:
        if ahora.tzinfo is None:
            ahora = ahora.replace(tzinfo=timezone.utc)
        return ahora - fecha_creacion
    else:
        ahora_naive = ahora.replace(tzinfo=None) if ahora.tzinfo is not None else ahora
        return ahora_naive - fecha_creacion


def _job_reintentar_inmediatas(db_session_factory: Callable[[], Session] | None = None) -> None:
    """
    Job programado que reintenta despachar avisos inmediatos no entregados.
    Reintenta notificaciones creadas entre hace 90 segundos y 15 minutos.
    Si persisten pendientes tras 15 a 17 minutos, envía una alerta al administrador.
    """
    if db_session_factory is None:
        from app.core.database import SessionLocal
        db_session_factory = SessionLocal

    db = db_session_factory()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_reintentar_inmediatas"):
            logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_reintentar_inmediatas",
            )
            return
        lock_adquirido = True

        ahora = ahora_argentina()
        tipos_inmediatos = [
            tipo for tipo, politica in POLITICA_WHATSAPP.items()
            if politica == "inmediata"
        ]

        pendientes = (
            db.query(Notificacion)
            .filter(
                Notificacion.canal_whatsapp == True,
                Notificacion.enviada_whatsapp == False,
                Notificacion.tipo.in_(tipos_inmediatos),
            )
            .all()
        )

        alertas_admin_ids: list[str] = []

        for notif in pendientes:
            edad = _calcular_edad(ahora, notif.created_at)

            # Ventana de reintento: entre 90 segundos y 15 minutos
            if VENTANA_REINTENTO_INMEDIATA_MIN <= edad <= VENTANA_REINTENTO_INMEDIATA_MAX:
                despachar_inmediata(db, notif.id)

            # Ventana de alerta admin: entre 15 y 17 minutos (una única notificación agrupada)
            elif VENTANA_ALERTA_ADMIN_MIN < edad <= VENTANA_ALERTA_ADMIN_MAX:
                alertas_admin_ids.append(str(notif.id))

        if alertas_admin_ids:
            enviar_alerta_admin(
                asunto="[Argentum] No salió un aviso de seguridad por WhatsApp",
                cuerpo=(
                    f"Se detectaron {len(alertas_admin_ids)} avisos de seguridad que no "
                    f"pudieron entregarse por WhatsApp. IDs de notificación: {alertas_admin_ids}"
                ),
                clave="whatsapp_seguridad_fallida",
            )

    except Exception as e:
        logger.exception("Error en _job_reintentar_inmediatas", error=str(e))
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_reintentar_inmediatas")
        db.close()
