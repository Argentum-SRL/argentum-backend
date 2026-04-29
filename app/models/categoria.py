from __future__ import annotations

from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Usuario


class TipoCategoria(str, Enum):
    INGRESO = "ingreso"
    EGRESO = "egreso"


class EstadoCategoria(str, Enum):
    ACTIVA = "activa"
    ARCHIVADA = "archivada"


class Categoria(Base):
    __tablename__ = "categorias"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre: Mapped[str] = mapped_column(String(100), nullable=False)
    tipo: Mapped[TipoCategoria] = mapped_column(
        SAEnum(TipoCategoria, values_callable=lambda obj: [e.value for e in obj], name="tipo_categoria_enum"), nullable=False
    )
    icono: Mapped[str | None] = mapped_column(String(50), nullable=True)
    color: Mapped[str | None] = mapped_column(String(7), nullable=True)
    es_global: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    creador_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=True
    )
    estado: Mapped[EstadoCategoria] = mapped_column(
        SAEnum(EstadoCategoria, values_callable=lambda obj: [e.value for e in obj], name="estado_categoria_enum"),
        nullable=False,
        default=EstadoCategoria.ACTIVA,
    )

    subcategorias: Mapped[list["Subcategoria"]] = relationship("Subcategoria")
    creador: Mapped[Usuario | None] = relationship("Usuario")

    def __repr__(self) -> str:
        return (
            "Categoria("
            f"id={self.id!r}, "
            f"nombre={self.nombre!r}, "
            f"tipo={self.tipo.value!r}, "
            f"es_global={self.es_global!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
