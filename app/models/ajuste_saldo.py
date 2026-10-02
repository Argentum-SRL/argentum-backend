from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.billetera import Billetera

from app.core.database import Base


class AjusteSaldo(Base):
    __tablename__ = "ajustes_saldo"
    __table_args__ = (
        Index("ix_ajustes_saldo_billetera_fecha", "billetera_id", "fecha"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    saldo_anterior: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    saldo_declarado: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    billetera: Mapped["Billetera"] = relationship("Billetera", back_populates="ajustes")

    def __repr__(self) -> str:
        return (
            "AjusteSaldo("
            f"id={self.id!r}, "
            f"billetera_id={self.billetera_id!r}, "
            f"monto={self.monto!r}, "
            f"saldo_anterior={self.saldo_anterior!r}, "
            f"saldo_declarado={self.saldo_declarado!r}, "
            f"fecha={self.fecha!r}"
            ")"
        )
