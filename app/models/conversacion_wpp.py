from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TipoMensajeWpp(str, Enum):
    TEXTO = "texto"
    AUDIO = "audio"


class ConversacionWpp(Base):
    __tablename__ = "conversaciones_wpp"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    mensaje_usuario: Mapped[str] = mapped_column(Text, nullable=False)
    tipo_mensaje: Mapped[TipoMensajeWpp] = mapped_column(
        SAEnum(TipoMensajeWpp, values_callable=lambda obj: [e.value for e in obj], name="tipo_mensaje_wpp_enum"),
        nullable=False,
        default=TipoMensajeWpp.TEXTO,
    )
    transcripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    mensaje_bot: Mapped[str] = mapped_column(Text, nullable=False)
    intent_detectado: Mapped[str | None] = mapped_column(String(100), nullable=True)
    entidades: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    accion_ejecutada: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confianza: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    slot_filling_activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    slot_filling_estado: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            "ConversacionWpp("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tipo_mensaje={self.tipo_mensaje.value!r}, "
            f"intent_detectado={self.intent_detectado!r}, "
            f"accion_ejecutada={self.accion_ejecutada!r}, "
            f"confianza={self.confianza!r}, "
            f"fecha={self.fecha!r}"
            ")"
        )
