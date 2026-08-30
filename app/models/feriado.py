from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Integer, String, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FeriadoAR(Base):
    __tablename__ = "feriados_ar"
    __table_args__ = (
        UniqueConstraint("fecha", name="uq_feriados_ar_fecha"),
        Index("ix_feriados_ar_fecha", "fecha", unique=True),
        Index("ix_feriados_ar_anio", "anio"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    nombre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    anio: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
