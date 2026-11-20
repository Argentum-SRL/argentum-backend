from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
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
    # La fila es la cola durable del webhook (fase_j3a_cola_sse);
    # payload guarda el mensaje de Meta solo hasta que se procesa y después queda en NULL.
    estado: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="listo",
        server_default="listo",
    )
    payload: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql"),
        nullable=True,
    )
    intentos: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    tomado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_mensajes_wpp_procesados_estado_fecha", "estado", "fecha_recepcion"),
    )

    def __repr__(self) -> str:
        return (
            f"MensajeWhatsappProcesado(id={self.id!r}, wamid={self.wamid!r}, "
            f"telefono={self.telefono!r}, fecha_recepcion={self.fecha_recepcion!r})"
        )
