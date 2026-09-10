from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.models.usuario import Usuario

from app.core.database import Base


class CalibracionUsuario(Base):
    __tablename__ = "calibraciones_usuario"
    __table_args__ = (
        UniqueConstraint("usuario_id", "moneda", name="uq_calibraciones_usuario_usuario_moneda"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    moneda: Mapped[str] = mapped_column(String(10), nullable=False)
    inicio_ciclo: Mapped[date] = mapped_column(Date, nullable=False)
    pasa_puerta: Mapped[bool] = mapped_column(Boolean, nullable=False)
    motivo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mensaje: Mapped[str | None] = mapped_column(Text, nullable=True)
    ciclos_evaluados: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cobertura_50: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    cobertura_80: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    cobertura_95: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    ancho_medio_80_rel: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    detalles: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    fecha_calculo: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    duracion_ms: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    usuario: Mapped["Usuario"] = relationship("Usuario")

    def __repr__(self) -> str:
        return (
            f"CalibracionUsuario(id={self.id!r}, usuario_id={self.usuario_id!r}, moneda={self.moneda!r}, "
            f"inicio_ciclo={self.inicio_ciclo!r}, pasa_puerta={self.pasa_puerta!r}, motivo={self.motivo!r}, "
            f"ciclos_evaluados={self.ciclos_evaluados!r}, cobertura_80={self.cobertura_80!r})"
        )
