from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UsoIA(Base):
    """
    Registro de consumo y costo estimado de llamadas a modelos de IA.
    No almacena mensajes de texto ni información personal del usuario.
    """
    __tablename__ = "uso_ia"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    usuario_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    funcion: Mapped[str] = mapped_column(String(40), nullable=False)
    modelo: Mapped[str] = mapped_column(String(60), nullable=False)
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    tokens_entrada_cache: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    segundos_audio: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    costo_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        default=Decimal("0.000000"),
        server_default=text("0"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<UsoIA id={self.id} fecha={self.fecha} funcion={self.funcion} "
            f"modelo={self.modelo} costo_usd={self.costo_usd}>"
        )
