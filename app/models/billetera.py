from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda, Usuario


class EstadoBilletera(str, Enum):
    ACTIVA = "activa"
    ARCHIVADA = "archivada"


class Billetera(Base):
    __tablename__ = "billeteras"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(
        SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False
    )
    saldo_actual: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    saldo_inicial: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    es_principal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    es_efectivo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    estado: Mapped[EstadoBilletera] = mapped_column(
        SAEnum(EstadoBilletera, values_callable=lambda obj: [e.value for e in obj], name="estado_billetera_enum"),
        nullable=False,
        default=EstadoBilletera.ACTIVA,
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    usuario: Mapped["Usuario"] = relationship("Usuario")

    def __repr__(self) -> str:
        return (
            "Billetera("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"nombre={self.nombre!r}, "
            f"moneda={self.moneda.value!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
