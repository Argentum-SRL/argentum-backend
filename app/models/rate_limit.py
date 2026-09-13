from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class RateLimit(Base):
    __tablename__ = "rate_limits"
    __table_args__ = (
        UniqueConstraint("accion", "identificador", name="uq_rate_limits_accion_identificador"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    accion: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    identificador: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    cantidad: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    ventana_segundos: Mapped[int] = mapped_column(Integer, nullable=False)
    ventana_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    detalles: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"RateLimit(id={self.id!r}, accion={self.accion!r}, "
            f"identificador={self.identificador!r}, cantidad={self.cantidad!r}, "
            f"expira_en={self.expira_en!r})"
        )
