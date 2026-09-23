from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.billetera import Billetera

from app.core.database import Base


class RendimientoBilletera(Base):
    __tablename__ = "rendimientos_billetera"
    __table_args__ = (
        Index("ix_rendimientos_billetera_billetera_id", "billetera_id"),
        Index("ix_rendimientos_billetera_fecha", "fecha"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    billetera: Mapped[Billetera] = relationship("Billetera", back_populates="rendimientos")

    def __repr__(self) -> str:
        return (
            "RendimientoBilletera("
            f"id={self.id!r}, "
            f"billetera_id={self.billetera_id!r}, "
            f"monto={self.monto!r}, "
            f"fecha={self.fecha!r}"
            ")"
        )
