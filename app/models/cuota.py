from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Cuota(Base):
    __tablename__ = "cuotas"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    grupo_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("grupos_cuotas.id"), nullable=False
    )
    transaccion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones.id"), nullable=False
    )
    numero_cuota: Mapped[int] = mapped_column(Integer, nullable=False)
    monto_proyectado: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    monto_real: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False)
    ajustada_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pagada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    grupo: Mapped["GrupoCuotas"] = relationship("GrupoCuotas", back_populates="cuotas")

    def __repr__(self) -> str:
        return (
            "Cuota("
            f"id={self.id!r}, "
            f"grupo_id={self.grupo_id!r}, "
            f"transaccion_id={self.transaccion_id!r}, "
            f"numero_cuota={self.numero_cuota!r}, "
            f"monto_proyectado={self.monto_proyectado!r}, "
            f"pagada={self.pagada!r}"
            ")"
        )
