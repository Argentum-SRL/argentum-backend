from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria
    from app.models.billetera import Billetera

from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class TipoTransaccion(str, Enum):
    INGRESO = "ingreso"
    EGRESO = "egreso"


class MetodoPago(str, Enum):
    EFECTIVO = "efectivo"
    DEBITO = "debito"
    TRANSFERENCIA = "transferencia"
    CREDITO = "credito"


class OrigenTransaccion(str, Enum):
    MANUAL = "manual"
    IA_WPP = "ia_wpp"
    IA_CHAT = "ia_chat"
    IA_PDF = "ia_pdf"
    RECURRENTE = "recurrente"


class EstadoVerificacionTransaccion(str, Enum):
    CONFIRMADA = "confirmada"
    PENDIENTE = "pendiente"


class Transaccion(Base):
    __tablename__ = "transacciones"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    tipo: Mapped[TipoTransaccion] = mapped_column(
        SAEnum(TipoTransaccion, values_callable=lambda obj: [e.value for e in obj], name="tipo_transaccion_enum"), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    descripcion: Mapped[str] = mapped_column(String(300), nullable=False)
    categoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=True
    )
    subcategoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subcategorias.id"), nullable=True
    )
    metodo_pago: Mapped[MetodoPago | None] = mapped_column(
        SAEnum(MetodoPago, values_callable=lambda obj: [e.value for e in obj], name="metodo_pago_enum"), nullable=True
    )
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    tarjeta_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tarjetas_credito.id"), nullable=True
    )
    es_recurrente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recurrente_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones_recurrentes.id"), nullable=True
    )
    es_cuota_hija: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Estas transacciones se excluyen de dashboard/graficos en la capa de consulta.
    es_padre_cuotas: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grupo_cuotas_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("grupos_cuotas.id"), nullable=True
    )
    origen: Mapped[OrigenTransaccion] = mapped_column(
        SAEnum(OrigenTransaccion, values_callable=lambda obj: [e.value for e in obj], name="origen_transaccion_enum"), nullable=False
    )
    estado_verificacion: Mapped[EstadoVerificacionTransaccion | None] = mapped_column(
        SAEnum(EstadoVerificacionTransaccion, values_callable=lambda obj: [e.value for e in obj], name="estado_verificacion_transaccion_enum"),
        nullable=True,
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    usuario: Mapped[Usuario] = relationship("Usuario")
    categoria: Mapped[Categoria | None] = relationship("Categoria")
    subcategoria: Mapped[Subcategoria | None] = relationship("Subcategoria")
    billetera: Mapped[Billetera] = relationship("Billetera")
    tarjeta: Mapped[TarjetaCredito | None] = relationship("TarjetaCredito")

    def __repr__(self) -> str:
        return (
            "Transaccion("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tipo={self.tipo.value!r}, "
            f"monto={self.monto!r}, "
            f"fecha={self.fecha!r}, "
            f"origen={self.origen.value!r}"
            ")"
        )
