# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AccionAdmin(Base):
    """
    Registro de auditoría de acciones realizadas desde el panel de administración.
    Se conserva durante 1 año sin IPs ni datos financieros.
    """

    __tablename__ = "acciones_admin"
    __table_args__ = (
        Index("ix_acciones_admin_fecha", "fecha"),
        Index("ix_acciones_admin_accion", "accion"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    admin_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    admin_nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    accion: Mapped[str] = mapped_column(String(40), nullable=False)
    usuario_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
    )
    usuario_texto: Mapped[str | None] = mapped_column(String(160), nullable=True)
    detalle: Mapped[str | None] = mapped_column(String(200), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AccionAdmin id={self.id} fecha={self.fecha} "
            f"accion={self.accion!r} admin_nombre={self.admin_nombre!r}>"
        )
