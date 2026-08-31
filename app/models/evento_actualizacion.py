from __future__ import annotations

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.core.database import Base


class EventoActualizacion(Base):
    __tablename__ = "eventos_actualizacion"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False, index=True)
    entidad = Column(String(50), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        Index("ix_eventos_actualizacion_usuario_created", "usuario_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"EventoActualizacion(id={self.id!r}, usuario_id={self.usuario_id!r}, "
            f"entidad={self.entidad!r}, created_at={self.created_at!r})"
        )
