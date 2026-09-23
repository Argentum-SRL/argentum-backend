from __future__ import annotations

from uuid import UUID
from sqlalchemy.orm import Session
from app.models.evento_actualizacion import EventoActualizacion


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

