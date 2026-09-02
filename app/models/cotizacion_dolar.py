"""
Modelo para el almacenamiento persistido del histórico de cotizaciones del dólar.

JUSTIFICACIÓN DE TIPO NUMERIC VS FLOAT:
En aplicaciones financieras, el uso de tipos de coma flotante (`float`) introduce
errores de redondeo binario acumulativos e imprecisión en la representación de
números decimales de base 10 (según el estándar IEEE 754). El tipo `Numeric`/`Decimal`
almacena valores decimales exactos con precisión fija, garantizando consistencia
matemática absoluta en cálculos de tipo de cambio, balances y transacciones monetarias
sin pérdida de centavos ni discrepancias en auditorías financieras.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CotizacionDolar(Base):
    """
    Tabla de cotizaciones diarias del dólar (oficial, blue, tarjeta, mep).
    Permite auditoría y consultas históricas por fecha sin consultar APIs externas.
    """
    __tablename__ = "cotizaciones_dolar"
    __table_args__ = (
        UniqueConstraint("fecha", "tipo", name="uq_cotizaciones_dolar_fecha_tipo"),
        Index("ix_cotizaciones_dolar_tipo_fecha", "tipo", "fecha"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    tipo: Mapped[str] = mapped_column(String(30), nullable=False)
    compra: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    venta: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    promedio: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            f"CotizacionDolar(fecha={self.fecha}, tipo={self.tipo!r}, "
            f"compra={self.compra}, venta={self.venta}, promedio={self.promedio})"
        )
