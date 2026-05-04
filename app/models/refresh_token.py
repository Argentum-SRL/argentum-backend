from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class RefreshToken(Base):
    __tablename__ = "user_refresh_tokens"

    # 1. id (UUID, PK)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # 2. usuario_id (UUID, FK -> usuarios.id)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )

    # 3. token_id (string, UNIQUE, NOT NULL)
    token_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    
    # 4. token_hash (string, NOT NULL)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # 5. fecha_expiracion (timestamp with timezone, NOT NULL)
    fecha_expiracion: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # 6. fecha_creacion (timestamp with timezone, NOT NULL)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # 7. revocado (boolean, default false)
    revocado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 8. device_info (string nullable)
    device_info: Mapped[str | None] = mapped_column(String(200), nullable=True)

    usuario = relationship("Usuario")