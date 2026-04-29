from __future__ import annotations

from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EstadoSubcategoria(str, Enum):
    ACTIVA = "activa"
    ARCHIVADA = "archivada"


class Subcategoria(Base):
    __tablename__ = "subcategorias"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    categoria_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    es_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    creador_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True
    )
    estado: Mapped[EstadoSubcategoria] = mapped_column(
        SAEnum(EstadoSubcategoria, values_callable=lambda obj: [e.value for e in obj], name="estado_subcategoria_enum"),
        nullable=False,
        default=EstadoSubcategoria.ACTIVA,
    )

    categoria: Mapped["Categoria"] = relationship("Categoria")

    def __repr__(self) -> str:
        return (
            "Subcategoria("
            f"id={self.id!r}, "
            f"categoria_id={self.categoria_id!r}, "
            f"nombre={self.nombre!r}, "
            f"es_global={self.es_global!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
