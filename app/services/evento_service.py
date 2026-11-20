from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID
from sqlalchemy import delete
from sqlalchemy.orm import Session
from app.models.evento_actualizacion import EventoActualizacion

logger = logging.getLogger(__name__)


_cache_contexto_financiero: dict[UUID, tuple[float, dict]] = {}


def emitir_evento_actualizacion(
    db: Session,
    usuario_id: UUID,
    entidad: str,
) -> EventoActualizacion:
    """
    Inserta una fila en eventos_actualizacion.
    No hace commit (el caller ya comitea).
    """
    _cache_contexto_financiero.pop(usuario_id, None)

    evento = EventoActualizacion(
        usuario_id=usuario_id,
        entidad=entidad,
    )
    db.add(evento)
    return evento


def purgar_eventos_actualizacion(
    db: Session,
    ahora: datetime | None = None,
) -> int:
    """
    Elimina eventos de actualización con más de 1 día de antigüedad.
    """
    ref = ahora if ahora is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    limite = ref - timedelta(days=1)
    n = int(
        db.execute(
            delete(EventoActualizacion).where(EventoActualizacion.created_at < limite)
        ).rowcount
        or 0
    )
    db.commit()
    logger.info("Purga de eventos_actualizacion: %d filas eliminadas.", n)
    return n

