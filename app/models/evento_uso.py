# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EventoUso(Base):
    """
    Registro anonimizado y agregado de eventos de uso de la web.
    Solo almacena eventos permitidos por usuario y por día, sin montos ni textos.
    """
    __tablename__ = "eventos_uso"
    __table_args__ = (
        UniqueConstraint("usuario_id", "evento", "dia", name="uq_eventos_uso_usuario_evento_dia"),
        Index("ix_eventos_uso_evento_dia", "evento", "dia"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    evento: Mapped[str] = mapped_column(String(40), nullable=False)
    dia: Mapped[date] = mapped_column(Date, nullable=False)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        return f"<EventoUso id={self.id} usuario_id={self.usuario_id} evento={self.evento} dia={self.dia}>"

