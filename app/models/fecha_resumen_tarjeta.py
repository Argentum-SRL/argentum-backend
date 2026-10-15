from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.tarjeta_credito import TarjetaCredito

from sqlalchemy import Date, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class FechaResumenTarjeta(Base):
    """
    Guarda las fechas reales de un resumen puntual de una tarjeta de crédito,
    pisando la regla de cierre y vencimiento para ese mes específico.
    """
    __tablename__ = "fechas_resumen_tarjeta"
    __table_args__ = (
        Index("ix_fechas_resumen_tarjeta_tarjeta_id", "tarjeta_id"),
        UniqueConstraint("tarjeta_id", "fecha_cierre", name="uq_fechas_resumen_tarjeta_tarjeta_id_fecha_cierre"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tarjeta_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tarjetas_credito.id", ondelete="CASCADE"), nullable=False
    )
    fecha_cierre: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    tarjeta: Mapped["TarjetaCredito"] = relationship("TarjetaCredito", back_populates="fechas_resumen")
