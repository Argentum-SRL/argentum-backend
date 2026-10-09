from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DecisionPatron(Base):
    """
    Decisión tomada por el usuario sobre un ítem de patrones financieros (Lo que se repite).
    Permite confirmar, descartar o mover ítems entre cajas.
    """
    __tablename__ = "decisiones_patrones"
    __table_args__ = (
        UniqueConstraint("usuario_id", "clave_item", name="uq_decisiones_patrones_usuario_clave"),
        CheckConstraint(
            "decision IN ('confirmado', 'descartado', 'movido')",
            name="chk_decisiones_patrones_decision",
        ),
        CheckConstraint(
            "caja_destino IS NULL OR caja_destino IN ('fijo', 'costumbre', 'dia_a_dia')",
            name="chk_decisiones_patrones_caja_destino",
        ),
        CheckConstraint(
            "(decision = 'movido' AND caja_destino IS NOT NULL) OR (decision != 'movido' AND caja_destino IS NULL)",
            name="chk_decisiones_patrones_caja_destino_movido",
        ),
        Index("ix_decisiones_patrones_usuario_id", "usuario_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    clave_item: Mapped[str] = mapped_column(String(200), nullable=False)
    decision: Mapped[str] = mapped_column(String(12), nullable=False)
    caja_destino: Mapped[str | None] = mapped_column(String(12), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    usuario: Mapped["Usuario"] = relationship("Usuario")

    def __repr__(self) -> str:
        return (
            f"DecisionPatron(id={self.id!r}, usuario_id={self.usuario_id!r}, "
            f"clave_item={self.clave_item!r}, decision={self.decision!r}, "
            f"caja_destino={self.caja_destino!r})"
        )
