from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.billetera import Billetera

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Integer
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class RedTarjeta(str, Enum):
    VISA = "visa"
    MASTERCARD = "mastercard"
    AMEX = "amex"
    NARANJA = "naranja"
    CABAL = "cabal"
    OTRO = "otro"


class EstadoTarjeta(str, Enum):
    ACTIVA = "activa"
    ARCHIVADA = "archivada"


class TarjetaCredito(Base):
    __tablename__ = "tarjetas_credito"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    red: Mapped[RedTarjeta] = mapped_column(
        SAEnum(RedTarjeta, values_callable=lambda obj: [e.value for e in obj], name="red_tarjeta_enum"),
        nullable=False,
        default=RedTarjeta.VISA,
    )
    dia_cierre: Mapped[int] = mapped_column(Integer, nullable=False)
    dia_vencimiento: Mapped[int] = mapped_column(Integer, nullable=False)
    limite_credito: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    moneda: Mapped[Moneda] = mapped_column(
        SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_tarjeta_enum"),
        nullable=False,
        default=Moneda.ARS,
    )
    estado: Mapped[EstadoTarjeta] = mapped_column(
        SAEnum(EstadoTarjeta, values_callable=lambda obj: [e.value for e in obj], name="estado_tarjeta_enum"),
        nullable=False,
        default=EstadoTarjeta.ACTIVA,
    )
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    usuario: Mapped["Usuario"] = relationship("Usuario")
    billetera: Mapped["Billetera"] = relationship("Billetera")

    def __repr__(self) -> str:
        return (
            "TarjetaCredito("
            f"id={self.id!r}, "
            f"nombre={self.nombre!r}, "
            f"red={self.red.value!r}, "
            f"moneda={self.moneda.value!r}"
            ")"
        )
