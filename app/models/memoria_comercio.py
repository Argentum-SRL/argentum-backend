from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria

from app.core.database import Base


class MemoriaComercio(Base):
    __tablename__ = "memoria_comercios"
    __table_args__ = (
        UniqueConstraint("usuario_id", "clave", "tipo", name="uq_memoria_comercios_usuario_clave_tipo"),
        Index("ix_memoria_comercios_usuario_id", "usuario_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    clave: Mapped[str] = mapped_column(String(120), nullable=False)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)
    categoria_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=False
    )
    subcategoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subcategorias.id"), nullable=True
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    fecha_actualizacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    usuario: Mapped["Usuario"] = relationship("Usuario")
    categoria: Mapped["Categoria"] = relationship("Categoria")
    subcategoria: Mapped["Subcategoria | None"] = relationship("Subcategoria")

    def __repr__(self) -> str:
        return (
            "MemoriaComercio("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"clave={self.clave!r}, "
            f"tipo={self.tipo!r}, "
            f"categoria_id={self.categoria_id!r}, "
            f"subcategoria_id={self.subcategoria_id!r}"
            ")"
        )
