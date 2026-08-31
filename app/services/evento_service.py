from __future__ import annotations

from uuid import UUID
from sqlalchemy.orm import Session
from app.models.evento_actualizacion import EventoActualizacion


def emitir_evento_actualizacion(
    db: Session,
    usuario_id: UUID,
    entidad: str,
) -> EventoActualizacion:
    """
    Inserta una fila en eventos_actualizacion.
    No hace commit (el caller ya comitea).
    """
    evento = EventoActualizacion(
        usuario_id=usuario_id,
        entidad=entidad,
    )
    db.add(evento)
    return evento
