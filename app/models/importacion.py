from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from typing import TYPE_CHECKING, Any

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.tarjeta_credito import TarjetaCredito
    from app.models.transaccion import Transaccion


class EstadoImportacion(str, enum.Enum):
    PROCESANDO = "procesando"
    PENDIENTE_REVISION = "pendiente_revision"
    IMPORTADO = "importado"
    ERROR = "error"
    CANCELADO = "cancelado"


class TipoCorreccion(str, enum.Enum):
    CATEGORIA_CAMBIADA = "categoria_cambiada"
    MONTO_AJUSTADO = "monto_ajustado"
    FECHA_AJUSTADA = "fecha_ajustada"
    CUOTA_CORREGIDA = "cuota_corregida"
    MARCADO_COMO_DUPLICADO = "marcado_como_duplicado"
    TRANSACCION_EXCLUIDA = "transaccion_excluida"
    TITULAR_REASIGNADO = "titular_reasignado"


class ImportacionResumen(Base):
    __tablename__ = "importaciones_resumen"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    admin_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    tarjeta_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tarjetas_credito.id"), nullable=True
    )
    banco_detectado: Mapped[str] = mapped_column(String(30), nullable=False)
    nombre_archivo: Mapped[str] = mapped_column(String(255), nullable=False)
    estado: Mapped[EstadoImportacion] = mapped_column(
        SAEnum(EstadoImportacion, values_callable=lambda obj: [e.value for e in obj], name="estado_importacion_enum"),
        nullable=False,
        default=EstadoImportacion.PROCESANDO,
        server_default="procesando"
    )
    capa_parser_usada: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confianza_extraccion: Mapped[Decimal | None] = mapped_column(Numeric(3, 2), nullable=True)
    periodo_desde: Mapped[date | None] = mapped_column(Date, nullable=True)
    periodo_hasta: Mapped[date | None] = mapped_column(Date, nullable=True)
    titulares_detectados: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    titulares_seleccionados: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    transacciones_parseadas: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    total_detectadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_importadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_duplicadas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_excluidas: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    mensaje_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    usuario: Mapped[Usuario] = relationship("Usuario", foreign_keys=[usuario_id])
    admin: Mapped[Usuario] = relationship("Usuario", foreign_keys=[admin_id])
    tarjeta: Mapped[TarjetaCredito | None] = relationship("TarjetaCredito")
    transacciones: Mapped[list[Transaccion]] = relationship("Transaccion", back_populates="importacion")
    correcciones: Mapped[list[CorreccionImportacion]] = relationship("CorreccionImportacion", back_populates="importacion")

    def __repr__(self) -> str:
        return (
            "ImportacionResumen("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tarjeta_id={self.tarjeta_id!r}, "
            f"banco_detectado={self.banco_detectado!r}, "
            f"nombre_archivo={self.nombre_archivo!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )


class CorreccionImportacion(Base):
    __tablename__ = "correcciones_importacion"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    importacion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("importaciones_resumen.id"), nullable=False
    )
    banco: Mapped[str] = mapped_column(String(30), nullable=False)
    capa_parser_usada: Mapped[str] = mapped_column(String(30), nullable=False)
    tipo_correccion: Mapped[TipoCorreccion] = mapped_column(
        SAEnum(TipoCorreccion, values_callable=lambda obj: [e.value for e in obj], name="tipo_correccion_enum"),
        nullable=False
    )
    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    importacion: Mapped[ImportacionResumen] = relationship("ImportacionResumen", back_populates="correcciones")

    def __repr__(self) -> str:
        return (
            "CorreccionImportacion("
            f"id={self.id!r}, "
            f"importacion_id={self.importacion_id!r}, "
            f"banco={self.banco!r}, "
            f"tipo_correccion={self.tipo_correccion.value!r}"
            ")"
        )
