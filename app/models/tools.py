from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IPCCache(Base):
    __tablename__ = "ipc_cache"
    __table_args__ = (
        CheckConstraint("indice_acumulado > 0", name="chk_ipc_indice_positivo"),
        CheckConstraint("length(fecha_dato) = 7", name="chk_ipc_fecha_formato"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    indice_acumulado: Mapped[float] = mapped_column(Float, nullable=False)
    fecha_dato: Mapped[str] = mapped_column(String, nullable=False, unique=True)  # "2026-04"
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    fuente: Mapped[str] = mapped_column(String, nullable=False, default="datos.gob.ar")
    es_estimado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
