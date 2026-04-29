from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CategoriaExcluida(Base):
    __tablename__ = "categorias_excluidas"
    __table_args__ = (
        CheckConstraint(
            "categoria_id IS NOT NULL OR subcategoria_id IS NOT NULL",
            name="ck_categoria_excluida_categoria_or_subcategoria",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    categoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=True
    )
    subcategoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subcategorias.id"), nullable=True
    )
    fecha_exclusion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            "CategoriaExcluida("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"categoria_id={self.categoria_id!r}, "
            f"subcategoria_id={self.subcategoria_id!r}"
            ")"
        )
