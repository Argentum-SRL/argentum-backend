"""
app/models/clave_idempotencia.py

Modelo de claves de idempotencia para registrar operaciones financieras únicas.
No guarda datos personales más allá de la respuesta de la propia operación.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ClaveIdempotencia(Base):
    """
    Registro de claves de idempotencia enviadas por el cliente.
    Asegura que una operación repetida devuelva la misma respuesta sin volver a ejecutarse.
    No guarda datos personales más allá de la respuesta de la propia operación.
    """
    __tablename__ = "claves_idempotencia"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clave: Mapped[str] = mapped_column(String(100), nullable=False)
    ruta: Mapped[str] = mapped_column(String(200), nullable=False)
    hash_cuerpo: Mapped[str] = mapped_column(String(64), nullable=False)
    codigo_http: Mapped[int] = mapped_column(Integer, nullable=False)
    respuesta: Mapped[Any] = mapped_column(JSONB, nullable=False)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("usuario_id", "clave", name="uq_claves_idempotencia_usuario_clave"),
        {"comment": "No guarda datos personales más allá de la respuesta de la propia operación."},
    )

    def __repr__(self) -> str:
        return (
            f"ClaveIdempotencia(id={self.id!r}, usuario_id={self.usuario_id!r}, "
            f"clave={self.clave!r}, ruta={self.ruta!r}, codigo_http={self.codigo_http!r})"
        )
