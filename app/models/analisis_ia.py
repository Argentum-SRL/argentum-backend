from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, Date, String, Text, text, func, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario

from app.core.database import Base


class AnalisisIA(Base):
    __tablename__ = "analisis_ia"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        default=uuid4
    )
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False
    )
    tipo_analisis: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        server_default="completo",
        default="completo"
    )
    ciclos_analizados: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )
    periodo_inicio: Mapped[date] = mapped_column(
        Date,
        nullable=False
    )
    periodo_fin: Mapped[date] = mapped_column(
        Date,
        nullable=False
    )
    perfil_detectado: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict
    )
    payload_enviado: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict
    )
    resultado: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
    resultado_secciones: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True
    )
    estado: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pendiente",
        default="pendiente"
    )
    error_detalle: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )
    modelo_usado: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        server_default="gpt-4o-mini",
        default="gpt-4o-mini"
    )
    input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )
    output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True
    )
    costo_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 6),
        nullable=True
    )
    generado_por: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="manual",
        default="manual"
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now()
    )

    # Relación con Usuario
    usuario: Mapped["Usuario"] = relationship("Usuario")

    # Índices en la tabla analisis_ia
    __table_args__ = (
        Index("ix_analisis_ia_usuario_id", "usuario_id"),
        Index("ix_analisis_ia_tipo_analisis", "tipo_analisis"),
        Index("ix_analisis_ia_usuario_periodo_tipo", "usuario_id", "periodo_fin", "tipo_analisis"),
        Index("ix_analisis_ia_creado_en_desc", text("creado_en DESC")),
    )

    def __repr__(self) -> str:
        return (
            "AnalisisIA("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tipo_analisis={self.tipo_analisis!r}, "
            f"estado={self.estado!r}, "
            f"modelo_usado={self.modelo_usado!r}, "
            f"creado_en={self.creado_en!r}"
            ")"
        )
