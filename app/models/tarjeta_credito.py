from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.billetera import Billetera

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Integer, Index
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
    __table_args__ = (
        Index("ix_tarjetas_credito_usuario_id", "usuario_id"),
        Index("ix_tarjetas_credito_billetera_id", "billetera_id"),
        Index("ix_tarjetas_credito_estado", "estado"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    apodo: Mapped[str | None] = mapped_column(String(50), nullable=True)

    red: Mapped[RedTarjeta] = mapped_column(
        SAEnum(RedTarjeta, values_callable=lambda obj: [e.value for e in obj], name="red_tarjeta_enum"),
        nullable=False,
        default=RedTarjeta.VISA,
    )
    dia_cierre: Mapped[int] = mapped_column(Integer, nullable=False)
    dia_vencimiento: Mapped[int] = mapped_column(Integer, nullable=False)
    limite_credito: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    moneda: Mapped[Moneda] = mapped_column(
        SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"),
        nullable=False,
        default=Moneda.ARS,
    )
    estado: Mapped[EstadoTarjeta] = mapped_column(
        SAEnum(EstadoTarjeta, values_callable=lambda obj: [e.value for e in obj], name="estado_tarjeta_enum"),
        nullable=False,
        default=EstadoTarjeta.ACTIVA,
    )
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    # Etapa 3C: Porcentaje de percepción sobre consumos en moneda extranjera al pesificar (ej. 30 para 30%).
    # IMPORTANTE: Es un porcentaje, no un factor (se almacena 30.00, no 0.30 ni 1.30).
    percepcion_moneda_extranjera: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=Decimal("30.00"),
        server_default="30.00",
        comment="Porcentaje de percepción sobre consumos en moneda extranjera (ej. 30 para 30%)"
    )
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
