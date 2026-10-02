from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TasaEntidad(Base):
    __tablename__ = "tasas_entidades"
    __table_args__ = (
        UniqueConstraint("fuente", "clave", "fecha_dato", name="uq_tasas_entidades_fuente_clave_fecha"),
        Index("ix_tasas_entidades_clave_fecha", "clave", "fecha_dato"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    fuente: Mapped[str] = mapped_column(String(30), nullable=False)
    clave: Mapped[str] = mapped_column(String(120), nullable=False)
    tna: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    tope: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    condiciones: Mapped[str | None] = mapped_column(String(300), nullable=True)
    fecha_dato: Mapped[date] = mapped_column(Date, nullable=False)
    vcp: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    vcp_anterior: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    fecha_dato_anterior: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_consulta: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            "TasaEntidad("
            f"id={self.id!r}, "
            f"fuente={self.fuente!r}, "
            f"clave={self.clave!r}, "
            f"tna={self.tna!r}, "
            f"fecha_dato={self.fecha_dato!r}"
            ")"
        )
