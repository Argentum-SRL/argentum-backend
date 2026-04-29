from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.usuario import Moneda


class TipoTransaccionRecurrente(str, Enum):
    INGRESO = "ingreso"
    EGRESO = "egreso"


class FrecuenciaTransaccionRecurrente(str, Enum):
    SEMANAL = "semanal"
    QUINCENAL = "quincenal"
    MENSUAL = "mensual"


class EstadoTransaccionRecurrente(str, Enum):
    ACTIVA = "activa"
    PAUSADA = "pausada"


class TransaccionRecurrente(Base):
    __tablename__ = "transacciones_recurrentes"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    tipo: Mapped[TipoTransaccionRecurrente] = mapped_column(
        SAEnum(TipoTransaccionRecurrente, values_callable=lambda obj: [e.value for e in obj], name="tipo_transaccion_recurrente_enum"), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False)
    categoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=True
    )
    subcategoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subcategorias.id"), nullable=True
    )
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    frecuencia: Mapped[FrecuenciaTransaccionRecurrente] = mapped_column(
        SAEnum(FrecuenciaTransaccionRecurrente, values_callable=lambda obj: [e.value for e in obj], name="frecuencia_transaccion_recurrente_enum"),
        nullable=False,
    )
    dia_registro: Mapped[int] = mapped_column(Integer, nullable=False)
    estado: Mapped[EstadoTransaccionRecurrente] = mapped_column(
        SAEnum(EstadoTransaccionRecurrente, values_callable=lambda obj: [e.value for e in obj], name="estado_transaccion_recurrente_enum"),
        nullable=False,
        default=EstadoTransaccionRecurrente.ACTIVA,
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            "TransaccionRecurrente("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tipo={self.tipo.value!r}, "
            f"monto={self.monto!r}, "
            f"frecuencia={self.frecuencia.value!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
