from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Date
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario

from app.core.database import Base


class HistorialPerfilFinanciero(Base):
    __tablename__ = "historial_perfiles_financieros"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    periodo_inicio: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    periodo_fin: Mapped[date] = mapped_column(Date, nullable=False)
    tasa_ahorro: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    score_impulsividad: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ratio_cuotas: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    cumplimiento_presupuesto: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    consistencia_registro: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    porcentaje_suscripciones: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    fecha_snapshot: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    usuario: Mapped["Usuario"] = relationship("Usuario")

    def __repr__(self) -> str:
        return (
            "HistorialPerfilFinanciero("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"periodo_inicio={self.periodo_inicio!r}, "
            f"periodo_fin={self.periodo_fin!r}, "
            f"tasa_ahorro={self.tasa_ahorro!r}, "
            f"score_impulsividad={self.score_impulsividad!r}, "
            f"ratio_cuotas={self.ratio_cuotas!r}, "
            f"cumplimiento_presupuesto={self.cumplimiento_presupuesto!r}, "
            f"consistencia_registro={self.consistencia_registro!r}, "
            f"porcentaje_suscripciones={self.porcentaje_suscripciones!r}"
            ")"
        )
