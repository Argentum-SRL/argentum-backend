# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy import JSON as sa_JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConsejoEmitido(Base):
    __tablename__ = "consejos_emitidos"
    __table_args__ = (
        UniqueConstraint("usuario_id", "tipo", "clave", name="uq_consejos_emitidos_usuario_tipo_clave"),
        Index("ix_consejos_emitidos_usuario_fecha", "usuario_id", "fecha_emision"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        nullable=False,
    )
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    clave: Mapped[str] = mapped_column(String(120), nullable=False)
    moneda: Mapped[str | None] = mapped_column(String(3), nullable=True)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    accion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    accion_datos: Mapped[dict[str, Any] | None] = mapped_column(sa_JSON(none_as_null=True), nullable=True)
    valores: Mapped[dict[str, Any]] = mapped_column(sa_JSON, nullable=False)
    fecha_emision: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_vence: Mapped[date] = mapped_column(Date, nullable=False)
    respuesta: Mapped[str | None] = mapped_column(String(12), nullable=True)
    fecha_respuesta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    medicion: Mapped[dict[str, Any] | None] = mapped_column(sa_JSON(none_as_null=True), nullable=True)
    fecha_medicion: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


class ConsejoSilenciado(Base):
    __tablename__ = "consejos_silenciados"

    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("usuarios.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tipo: Mapped[str] = mapped_column(String(30), primary_key=True)
    fecha: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
