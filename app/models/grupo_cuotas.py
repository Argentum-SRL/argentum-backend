from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class GrupoCuotas(Base):
    __tablename__ = "grupos_cuotas"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    transaccion_padre_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones.id"), nullable=False
    )
    descripcion: Mapped[str] = mapped_column(String(300), nullable=False)
    monto_total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    cantidad_cuotas: Mapped[int] = mapped_column(Integer, nullable=False)
    tiene_interes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tasa_interes: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    total_financiado: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    cuotas: Mapped[list["Cuota"]] = relationship("Cuota", back_populates="grupo")

    def __repr__(self) -> str:
        return (
            "GrupoCuotas("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"transaccion_padre_id={self.transaccion_padre_id!r}, "
            f"monto_total={self.monto_total!r}, "
            f"cantidad_cuotas={self.cantidad_cuotas!r}, "
            f"moneda={self.moneda.value!r}"
            ")"
        )
