from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CodigoVerificacion(Base):
    __tablename__ = "codigos_verificacion"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tipo: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    identificador: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    codigo: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    expiracion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    intentos_fallidos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_intentos: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    consumido: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    consumido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"CodigoVerificacion(id={self.id!r}, tipo={self.tipo!r}, "
            f"identificador={self.identificador!r}, codigo={self.codigo!r}, "
            f"expiracion={self.expiracion!r}, consumido={self.consumido!r})"
        )
