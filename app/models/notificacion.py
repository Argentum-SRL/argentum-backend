from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TipoNotificacion(str, Enum):
    PRESUPUESTO_80 = "presupuesto_80"
    PRESUPUESTO_100 = "presupuesto_100"
    CUOTA_VENCE = "cuota_vence"
    SUSCRIPCION_COBRO = "suscripcion_cobro"
    META_PROXIMA = "meta_proxima"
    SUGERENCIA_PRESUPUESTO = "sugerencia_presupuesto"
    RESUMEN_SEMANAL = "resumen_semanal"
    RESUMEN_MENSUAL = "resumen_mensual"
    IA_PENDIENTE = "ia_pendiente"
    INACTIVIDAD = "inactividad"


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    tipo: Mapped[TipoNotificacion] = mapped_column(
        SAEnum(TipoNotificacion, values_callable=lambda obj: [e.value for e in obj], name="tipo_notificacion_enum"), nullable=False
    )
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    mensaje: Mapped[str] = mapped_column(Text, nullable=False)
    leida: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    modulo_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            "Notificacion("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tipo={self.tipo.value!r}, "
            f"titulo={self.titulo!r}, "
            f"leida={self.leida!r}"
            ")"
        )
