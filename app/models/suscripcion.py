from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class FrecuenciaSuscripcion(str, Enum):
    MENSUAL = "mensual"
    BIMESTRAL = "bimestral"
    TRIMESTRAL = "trimestral"
    SEMESTRAL = "semestral"
    ANUAL = "anual"


class EstadoSuscripcion(str, Enum):
    ACTIVA = "activa"
    PAUSADA = "pausada"
    CANCELADA = "cancelada"


class Suscripcion(Base):
    __tablename__ = "suscripciones"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    categoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=True
    )
    frecuencia: Mapped[FrecuenciaSuscripcion] = mapped_column(
        SAEnum(FrecuenciaSuscripcion, values_callable=lambda obj: [e.value for e in obj], name="frecuencia_suscripcion_enum"), nullable=False
    )
    proximo_cobro: Mapped[date] = mapped_column(Date, nullable=False)
    estado: Mapped[EstadoSuscripcion] = mapped_column(
        SAEnum(EstadoSuscripcion, values_callable=lambda obj: [e.value for e in obj], name="estado_suscripcion_enum"),
        nullable=False,
        default=EstadoSuscripcion.ACTIVA,
    )
    # El monto vigente se toma del ultimo HistorialSuscripcion, no se persiste aca.
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    historial: Mapped[list["HistorialSuscripcion"]] = relationship("HistorialSuscripcion")

    def __repr__(self) -> str:
        return (
            "Suscripcion("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"nombre={self.nombre!r}, "
            f"frecuencia={self.frecuencia.value!r}, "
            f"proximo_cobro={self.proximo_cobro!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
