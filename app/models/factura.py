from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria
    from app.models.transaccion import Transaccion

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class Factura(Base):
    __tablename__ = "facturas"
    __table_args__ = (
        CheckConstraint("monto > 0", name="chk_facturas_monto_positivo"),
        CheckConstraint("estado IN ('pendiente', 'pagada', 'descartada')", name="chk_facturas_estado"),
        CheckConstraint("origen IN ('whatsapp_foto', 'whatsapp_pdf')", name="chk_facturas_origen"),
        Index("ix_facturas_usuario_estado_vencimiento", "usuario_id", "estado", "fecha_vencimiento"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    descripcion: Mapped[str] = mapped_column(String(120), nullable=False)
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(
        SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum", create_type=False),
        nullable=False,
    )
    fecha_vencimiento: Mapped[date] = mapped_column(Date, nullable=False)
    categoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=True
    )
    subcategoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subcategorias.id"), nullable=True
    )
    estado: Mapped[str] = mapped_column(String(12), nullable=False, default="pendiente", server_default="pendiente")
    pagada_automaticamente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    transaccion_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones.id", ondelete="SET NULL"), nullable=True
    )
    origen: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(), default=lambda: datetime.now(timezone.utc)
    )

    # Relaciones
    usuario: Mapped[Usuario] = relationship("Usuario")
    categoria: Mapped[Categoria | None] = relationship("Categoria")
    subcategoria: Mapped[Subcategoria | None] = relationship("Subcategoria")
    transaccion: Mapped[Transaccion | None] = relationship("Transaccion")
