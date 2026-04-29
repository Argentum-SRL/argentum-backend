from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class HistorialSuscripcion(Base):
    __tablename__ = "historial_suscripciones"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    suscripcion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("suscripciones.id"), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    vigente_desde: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    suscripcion: Mapped["Suscripcion"] = relationship("Suscripcion", back_populates="historial")

    def __repr__(self) -> str:
        return (
            "HistorialSuscripcion("
            f"id={self.id!r}, "
            f"suscripcion_id={self.suscripcion_id!r}, "
            f"monto={self.monto!r}, "
            f"moneda={self.moneda.value!r}, "
            f"vigente_desde={self.vigente_desde!r}"
            ")"
        )
