from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class TipoMovimientoMeta(str, Enum):
    APORTE = "aporte"
    RETIRO = "retiro"


class MovimientoMeta(Base):
    __tablename__ = "movimientos_meta"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    meta_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("metas.id"), nullable=False
    )
    tipo: Mapped[TipoMovimientoMeta] = mapped_column(
        SAEnum(TipoMovimientoMeta, values_callable=lambda obj: [e.value for e in obj], name="tipo_movimiento_meta_enum"), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda_movimiento: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    cotizacion_usada: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    tipo_dolar_usado: Mapped[str | None] = mapped_column(String(30), nullable=True)
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    meta: Mapped["Meta"] = relationship("Meta", back_populates="movimientos")

    def __repr__(self) -> str:
        return (
            "MovimientoMeta("
            f"id={self.id!r}, "
            f"meta_id={self.meta_id!r}, "
            f"tipo={self.tipo.value!r}, "
            f"monto={self.monto!r}, "
            f"moneda_movimiento={self.moneda_movimiento.value!r}, "
            f"fecha={self.fecha!r}"
            ")"
        )
