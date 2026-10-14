"""
Modelo para el registro de ejecuciones de tareas automáticas (APScheduler).
Permite monitorear el historial, estado, duración y errores de cada tarea.
"""
from __future__ import annotations

import uuid
from sqlalchemy import Column, String, DateTime, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class EjecucionJob(Base):
    __tablename__ = "ejecuciones_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(String(100), nullable=False, index=True)
    inicio = Column(DateTime(timezone=True), nullable=False, index=True)
    fin = Column(DateTime(timezone=True), nullable=False)
    resultado = Column(String(20), nullable=False)  # "ok", "error", "omitido"
    duracion_ms = Column(Integer, nullable=False)
    detalle = Column(String(300), nullable=True)

    def __repr__(self) -> str:
        return (
            f"EjecucionJob(id={self.id!r}, job_id={self.job_id!r}, "
            f"resultado={self.resultado!r}, duracion_ms={self.duracion_ms!r})"
        )
