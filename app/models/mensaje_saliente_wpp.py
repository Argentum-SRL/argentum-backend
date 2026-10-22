"""
app/models/mensaje_saliente_wpp.py — Modelo de registro para mensajes salientes de WhatsApp.
Comentario: No guarda textos de mensajes ni números de teléfono en claro.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MensajeSalienteWpp(Base):
    """
    Registro estructurado de mensajes salientes de WhatsApp (respuestas, plantillas y avisos).
    No almacena el contenido textual del mensaje ni teléfonos en claro por privacidad y seguridad.
    """
    __tablename__ = "mensajes_salientes_wpp"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    usuario_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    telefono_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tipo: Mapped[str] = mapped_column(String(60), nullable=False)
    es_plantilla: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    partes: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    aviso_tipo: Mapped[str | None] = mapped_column(String(40), nullable=True)
    wamid_entrante: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    enviado: Mapped[bool] = mapped_column(Boolean, nullable=False)
    motivo_no_enviado: Mapped[str | None] = mapped_column(String(40), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codigo_error_meta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wamid_meta: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    estado_meta: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tipo_cobro: Mapped[str | None] = mapped_column(String(40), nullable=True)
    categoria_cobro: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
