from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.tarjeta_credito import TarjetaCredito
    from app.models.transaccion import Transaccion

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, Index, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class EstadoSaldoArrastrado(str, Enum):
    ACTIVO = "activo"
    SALDADO = "saldado"


class SaldoArrastradoTarjeta(Base):
    """
    Modelo de Saldo Arrastrado (Financiado) de Resumen de Tarjeta de Crédito (Etapa 3B).

    JUSTIFICACIÓN DEL DISEÑO DE SALDO ARRASTRADO:
    1. Entidad propia vs cuotas:
       En el sistema de tarjetas de crédito de la República Argentina, cuando un resumen
       se abona parcialmente, las compras y cuotas del período NO quedan a medio pagar:
       se consideran facturadas y saldadas en su totalidad. El importe impago se consolida
       como un saldo financiado ('revolving credit') que pasa al período siguiente sujeto
       a las tasas de interés que el banco emisor determine. Por ende, modelarlo como una
       entidad propia asociada a la tarjeta y al vencimiento del resumen refleja con exactitud
       la realidad bancaria argentina y evita marcar cuotas individuales como 'parcialmente pagadas'.
    2. Vínculo a (tarjeta_id, fecha_vencimiento_resumen):
       Cada saldo arrastrado pertenece a un resumen con fecha de vencimiento concreta. Esto
       garantiza la trazabilidad histórica estricta, permitiendo saber en qué período nació,
       cuánto se facturó originalmente, cuánto se pagó y qué remanente quedó debiendo.
    3. Restricción de unicidad activa (uq_saldos_arrastrados_tarjeta_resumen_activo):
       Un índice único parcial sobre (tarjeta_id, fecha_vencimiento_resumen) donde estado = 'activo'
       asegura a nivel motor de base de datos que ningún resumen posea más de un saldo arrastrado
       activo en simultáneo, previniendo duplicaciones e inconsistencias operativas.
    4. Trazabilidad de origen y reducciones (dualidad transaccional):
       `transaccion_origen_id` enlaza con la transacción de pago parcial que generó el saldo.
       La relación con `PagoSaldoArrastrado` rastrea cronológicamente cada transacción de pago
       posterior que redujo o saldó dicho saldo arrastrado. Gracias a este diseño, si el usuario
       elimina un pago posterior, el saldo se restaura a su monto anterior; y si elimina el pago
       originario, el saldo arrastrado se extingue por completo.
    5. Tipado Numeric:
       Todos los importes monetarios se almacenan como Numeric(15, 2), mapeados estrictamente
       a Decimal en Python, asegurando precisión aritmética exacta libre de errores de coma flotante.
    """
    __tablename__ = "saldos_arrastrados_tarjeta"
    __table_args__ = (
        Index(
            "uq_saldos_arrastrados_tarjeta_resumen_activo",
            "tarjeta_id",
            "fecha_vencimiento_resumen",
            unique=True,
            postgresql_where=text("estado = 'activo'")
        ),
        Index("ix_saldos_arrastrados_tarjeta_id", "tarjeta_id"),
        Index("ix_saldos_arrastrados_vencimiento", "fecha_vencimiento_resumen"),
        Index("ix_saldos_arrastrados_transaccion_origen", "transaccion_origen_id"),
        Index("ix_saldos_arrastrados_tarjeta_estado", "tarjeta_id", "estado"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tarjeta_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tarjetas_credito.id", ondelete="CASCADE"), nullable=False
    )
    fecha_vencimiento_resumen: Mapped[date] = mapped_column(Date, nullable=False)
    monto_inicial: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    monto_restante: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(
        SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"),
        nullable=False,
        default=Moneda.ARS
    )
    estado: Mapped[EstadoSaldoArrastrado] = mapped_column(
        SAEnum(
            EstadoSaldoArrastrado,
            values_callable=lambda obj: [e.value for e in obj],
            name="estado_saldo_arrastrado_enum"
        ),
        nullable=False,
        default=EstadoSaldoArrastrado.ACTIVO
    )
    transaccion_origen_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones.id", ondelete="CASCADE"), nullable=False
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    fecha_modificacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    tarjeta: Mapped["TarjetaCredito"] = relationship("TarjetaCredito")
    transaccion_origen: Mapped["Transaccion"] = relationship("Transaccion", foreign_keys=[transaccion_origen_id])
    pagos: Mapped[list["PagoSaldoArrastrado"]] = relationship(
        "PagoSaldoArrastrado",
        back_populates="saldo_arrastrado",
        cascade="all, delete-orphan",
        order_by="PagoSaldoArrastrado.fecha_creacion"
    )

    def __repr__(self) -> str:
        return (
            "SaldoArrastradoTarjeta("
            f"id={self.id!r}, "
            f"tarjeta_id={self.tarjeta_id!r}, "
            f"vencimiento={self.fecha_vencimiento_resumen!r}, "
            f"monto_restante={self.monto_restante!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )


class PagoSaldoArrastrado(Base):
    """
    Vinculación de amortización de saldo arrastrado (Etapa 3B).
    Registra cada transacción de pago posterior que redujo o saldó un saldo arrastrado,
    permitiendo reversión exacta si la transacción de pago se elimina.
    """
    __tablename__ = "pagos_saldo_arrastrado"
    __table_args__ = (
        Index("ix_pagos_saldo_arrastrado_saldo_id", "saldo_arrastrado_id"),
        Index("ix_pagos_saldo_arrastrado_tx_id", "transaccion_pago_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    saldo_arrastrado_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("saldos_arrastrados_tarjeta.id", ondelete="CASCADE"), nullable=False
    )
    transaccion_pago_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones.id", ondelete="CASCADE"), nullable=False
    )
    monto_aplicado: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    saldo_arrastrado: Mapped[SaldoArrastradoTarjeta] = relationship(
        "SaldoArrastradoTarjeta", back_populates="pagos"
    )
    transaccion_pago: Mapped["Transaccion"] = relationship(
        "Transaccion", foreign_keys=[transaccion_pago_id]
    )

    def __repr__(self) -> str:
        return (
            "PagoSaldoArrastrado("
            f"id={self.id!r}, "
            f"saldo_arrastrado_id={self.saldo_arrastrado_id!r}, "
            f"transaccion_pago_id={self.transaccion_pago_id!r}, "
            f"monto_aplicado={self.monto_aplicado!r}"
            ")"
        )
