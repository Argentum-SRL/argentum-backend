# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ExpectativaInflacion(Base):
    """
    Modelo canónico para registrar expectativas de inflación relevadas por el REM del BCRA.
    """
    __tablename__ = "expectativas_inflacion"
    __table_args__ = (
        UniqueConstraint("relevamiento", "periodo", name="uq_expectativas_inflacion_relevamiento_periodo"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    relevamiento: Mapped[str] = mapped_column(String(7), nullable=False)  # "AAAA-MM"
    periodo: Mapped[str] = mapped_column(String(7), nullable=False)  # "AAAA-MM" o "prox12"
    valor: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)  # porcentaje, mediana
    fuente: Mapped[str] = mapped_column(String(20), nullable=False, default="REM BCRA", server_default="REM BCRA")
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

