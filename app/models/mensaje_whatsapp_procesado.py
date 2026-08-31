from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MensajeWhatsappProcesado(Base):
    __tablename__ = "mensajes_whatsapp_procesados"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    wamid: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    telefono: Mapped[str | None] = mapped_column(String(50), nullable=True)
    tipo_mensaje: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fecha_recepcion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"MensajeWhatsappProcesado(id={self.id!r}, wamid={self.wamid!r}, "
            f"telefono={self.telefono!r}, fecha_recepcion={self.fecha_recepcion!r})"
        )
