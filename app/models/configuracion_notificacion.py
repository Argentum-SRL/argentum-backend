from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.notificacion import TipoNotificacion


class ConfiguracionNotificacion(Base):
    __tablename__ = "configuraciones_notificacion"
    __table_args__ = (
        UniqueConstraint("usuario_id", "tipo", name="uq_configuracion_notificacion_usuario_tipo"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    tipo: Mapped[TipoNotificacion] = mapped_column(
        SAEnum(TipoNotificacion, values_callable=lambda obj: [e.value for e in obj], name="tipo_notificacion_enum"), nullable=False
    )
    canal_wpp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    canal_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    anticipacion_dias: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            "ConfiguracionNotificacion("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tipo={self.tipo.value!r}, "
            f"canal_wpp={self.canal_wpp!r}, "
            f"canal_app={self.canal_app!r}, "
            f"anticipacion_dias={self.anticipacion_dias!r}"
            ")"
        )
