from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class PeriodoPresupuestoTipo(str, Enum):
    SEMANAL = "semanal"
    QUINCENAL = "quincenal"
    MENSUAL = "mensual"


class RenovacionPresupuesto(str, Enum):
    AUTOMATICA = "automatica"
    MANUAL = "manual"


class EstadoPresupuesto(str, Enum):
    ACTIVO = "activo"
    PAUSADO = "pausado"
    FINALIZADO = "finalizado"


class Presupuesto(Base):
    __tablename__ = "presupuestos"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    periodo: Mapped[PeriodoPresupuestoTipo] = mapped_column(
        SAEnum(PeriodoPresupuestoTipo, values_callable=lambda obj: [e.value for e in obj], name="periodo_presupuesto_enum"), nullable=False
    )
    renovacion: Mapped[RenovacionPresupuesto] = mapped_column(
        SAEnum(RenovacionPresupuesto, values_callable=lambda obj: [e.value for e in obj], name="renovacion_presupuesto_enum"), nullable=False
    )
    estado: Mapped[EstadoPresupuesto] = mapped_column(
        SAEnum(EstadoPresupuesto, values_callable=lambda obj: [e.value for e in obj], name="estado_presupuesto_enum"),
        nullable=False,
        default=EstadoPresupuesto.ACTIVO,
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    categorias: Mapped[list["Categoria"]] = relationship(
        "Categoria",
        secondary="presupuestos_categorias",
    )
    periodos: Mapped[list["PeriodoPresupuesto"]] = relationship("PeriodoPresupuesto")

    def __repr__(self) -> str:
        return (
            "Presupuesto("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"nombre={self.nombre!r}, "
            f"monto={self.monto!r}, "
            f"moneda={self.moneda.value!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
