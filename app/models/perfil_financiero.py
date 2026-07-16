from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario

from app.core.database import Base


class PerfilFinanciero(Base):
    __tablename__ = "perfiles_financieros"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), unique=True, nullable=False
    )
    tasa_ahorro_ars: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    tasa_ahorro_usd: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    score_impulsividad_ars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_impulsividad_usd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ratio_cuotas_ars: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    ratio_cuotas_usd: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    cumplimiento_presupuesto: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    consistencia_registro: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    porcentaje_suscripciones_ars: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    porcentaje_suscripciones_usd: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    ultima_actualizacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    usuario: Mapped["Usuario"] = relationship("Usuario")

    def __repr__(self) -> str:
        return (
            "PerfilFinanciero("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tasa_ahorro_ars={self.tasa_ahorro_ars!r}, "
            f"tasa_ahorro_usd={self.tasa_ahorro_usd!r}, "
            f"score_impulsividad_ars={self.score_impulsividad_ars!r}, "
            f"score_impulsividad_usd={self.score_impulsividad_usd!r}, "
            f"ratio_cuotas_ars={self.ratio_cuotas_ars!r}, "
            f"ratio_cuotas_usd={self.ratio_cuotas_usd!r}, "
            f"cumplimiento_presupuesto={self.cumplimiento_presupuesto!r}, "
            f"consistencia_registro={self.consistencia_registro!r}, "
            f"porcentaje_suscripciones_ars={self.porcentaje_suscripciones_ars!r}, "
            f"porcentaje_suscripciones_usd={self.porcentaje_suscripciones_usd!r}"
            ")"
        )
