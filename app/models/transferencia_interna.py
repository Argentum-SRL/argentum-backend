from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.billetera import Billetera
    from app.models.transaccion import Transaccion

from app.core.database import Base
from app.models.usuario import Moneda


class TransferenciaInterna(Base):
    __tablename__ = "transferencias_internas"
    __table_args__ = (
        Index("ix_transferencias_internas_usuario_fecha", "usuario_id", "fecha"),
        Index("ix_transferencias_internas_billetera_origen_id", "billetera_origen_id"),
        Index("ix_transferencias_internas_billetera_destino_id", "billetera_destino_id"),
        Index("ix_transferencias_internas_transaccion_comision_id", "transaccion_comision_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    billetera_origen_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    billetera_destino_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    # Monto y moneda base (monto que sale de origen y moneda de origen, para retrocompatibilidad)
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)

    # Campos bimonetarios (Etapa 4): montos y monedas específicos de origen y destino
    monto_origen: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    monto_destino: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    moneda_origen: Mapped[Moneda | None] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=True)
    moneda_destino: Mapped[Moneda | None] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=True)

    # Cotización implícita: ARS / USD (pesos por dólar) para compra o venta de moneda extranjera
    cotizacion: Mapped[Decimal | None] = mapped_column(Numeric(15, 4), nullable=True)

    # Comisión opcional de la operación (gasto real)
    transaccion_comision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones.id", ondelete="SET NULL"), nullable=True
    )
    monto_comision: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    moneda_comision: Mapped[Moneda | None] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=True)

    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    notas: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Solo impacta saldo_actual entre billeteras, no se usa en dashboard/balance/proyecciones.
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    usuario: Mapped[Usuario] = relationship("Usuario")
    billetera_origen: Mapped[Billetera] = relationship("Billetera", foreign_keys=[billetera_origen_id])
    billetera_destino: Mapped[Billetera] = relationship("Billetera", foreign_keys=[billetera_destino_id])
    transaccion_comision: Mapped[Transaccion | None] = relationship("Transaccion", foreign_keys=[transaccion_comision_id])

    def __repr__(self) -> str:
        return (
            "TransferenciaInterna("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"billetera_origen_id={self.billetera_origen_id!r}, "
            f"billetera_destino_id={self.billetera_destino_id!r}, "
            f"monto={self.monto!r}, "
            f"moneda={self.moneda.value!r}, "
            f"monto_destino={self.monto_destino!r}, "
            f"cotizacion={self.cotizacion!r}, "
            f"fecha={self.fecha!r}"
            ")"
        )
