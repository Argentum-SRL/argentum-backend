from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.movimiento_meta import MovimientoMeta


class EstadoMeta(str, Enum):
    ACTIVA = "activa"
    COMPLETADA = "completada"
    PAUSADA = "pausada"


class Meta(Base):
    __tablename__ = "metas"
    __table_args__ = (
        CheckConstraint("monto_objetivo > 0", name="chk_meta_monto_objetivo_gt_zero"),
        CheckConstraint("monto_actual >= 0", name="chk_meta_monto_actual_gte_zero"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    monto_objetivo: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    monto_actual: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    fecha_limite: Mapped[date | None] = mapped_column(Date, nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    nota: Mapped[str | None] = mapped_column(Text, nullable=True)
    estado: Mapped[EstadoMeta] = mapped_column(
        SAEnum(EstadoMeta, values_callable=lambda obj: [e.value for e in obj], name="estado_meta_enum"), nullable=False, default=EstadoMeta.ACTIVA
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    usuario: Mapped[Usuario] = relationship("Usuario")

    # La meta no queda atada a una billetera fija; cada movimiento define origen/destino.
    movimientos: Mapped[list[MovimientoMeta]] = relationship(
        "MovimientoMeta",
        back_populates="meta",
        order_by="desc(MovimientoMeta.fecha), desc(MovimientoMeta.fecha_creacion)"
    )

    def __repr__(self) -> str:
        return (
            "Meta("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"nombre={self.nombre!r}, "
            f"monto_objetivo={self.monto_objetivo!r}, "
            f"monto_actual={self.monto_actual!r}, "
            f"moneda={self.moneda.value!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
