from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PeriodoPresupuesto(Base):
    __tablename__ = "periodos_presupuesto"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    presupuesto_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("presupuestos.id"), nullable=False
    )
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date] = mapped_column(Date, nullable=False)
    monto_limite: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    monto_usado: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    superado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            "PeriodoPresupuesto("
            f"id={self.id!r}, "
            f"presupuesto_id={self.presupuesto_id!r}, "
            f"fecha_inicio={self.fecha_inicio!r}, "
            f"fecha_fin={self.fecha_fin!r}, "
            f"monto_limite={self.monto_limite!r}, "
            f"monto_usado={self.monto_usado!r}, "
            f"superado={self.superado!r}"
            ")"
        )
