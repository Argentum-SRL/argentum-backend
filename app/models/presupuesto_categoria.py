from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PresupuestoCategoria(Base):
    __tablename__ = "presupuestos_categorias"
    __table_args__ = (
        CheckConstraint(
            "categoria_id IS NOT NULL OR subcategoria_id IS NOT NULL",
            name="ck_presupuesto_categoria_categoria_or_subcategoria",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    presupuesto_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("presupuestos.id"), nullable=False
    )
    categoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=True
    )
    subcategoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subcategorias.id"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            "PresupuestoCategoria("
            f"id={self.id!r}, "
            f"presupuesto_id={self.presupuesto_id!r}, "
            f"categoria_id={self.categoria_id!r}, "
            f"subcategoria_id={self.subcategoria_id!r}"
            ")"
        )
