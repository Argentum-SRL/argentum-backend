from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.usuario import Usuario
    from app.models.categoria import Categoria
    from app.models.subcategoria import Subcategoria
    from app.models.billetera import Billetera
    from app.models.importacion import ImportacionResumen
    from app.models.tarjeta_credito import TarjetaCredito

from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Index, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.usuario import Moneda


class TipoTransaccion(str, Enum):
    INGRESO = "ingreso"
    EGRESO = "egreso"


class MetodoPago(str, Enum):
    EFECTIVO = "efectivo"
    DEBITO = "debito"
    TRANSFERENCIA = "transferencia"
    CREDITO = "credito"


class OrigenTransaccion(str, Enum):
    MANUAL = "manual"
    IA_WPP = "ia_wpp"
    IA_CHAT = "ia_chat"
    IA_PDF = "ia_pdf"
    RECURRENTE = "recurrente"


class EstadoVerificacionTransaccion(str, Enum):
    CONFIRMADA = "confirmada"
    PENDIENTE = "pendiente"


class Transaccion(Base):
    """
    Modelo de transacciones de ingresos y egresos.

    REGLA DE CAMPOS DE TRAZABILIDAD MULTIMONEDA:
    Los campos `monto_original`, `moneda_original`, `cotizacion_aplicada` y `tipo_dolar_usado`
    se completan ÚNICAMENTE cuando existió una conversión de moneda en la transacción
    (por ejemplo, un gasto realizado en USD liquidado sobre una billetera en ARS, o viceversa).
    Si el movimiento se registró originalmente en la misma moneda de la billetera, estos campos
    permanecen estrictamente NULOS (None). Nunca se duplica información.

    DELIBERACIÓN DE LA ETAPA 2:
    En esta etapa de infraestructura, estos 4 campos quedan sin utilizar en la lógica de negocio.
    Su creación prepara el esquema de datos para la Etapa 3 en adelante, donde se introducirá
    la conversión automática. Ningún endpoint ni esquema Pydantic expone estos campos aún.
    """
    __tablename__ = "transacciones"
    __table_args__ = (
        Index("ix_transacciones_usuario_id", "usuario_id"),
        Index("ix_transacciones_usuario_fecha", "usuario_id", "fecha"),
        Index("ix_transacciones_usuario_tipo_fecha", "usuario_id", "tipo", "fecha"),
        Index("ix_transacciones_billetera_id", "billetera_id"),
        Index("ix_transacciones_categoria_id", "categoria_id"),
        Index("ix_transacciones_subcategoria_id", "subcategoria_id"),
        Index("ix_transacciones_tarjeta_id", "tarjeta_id"),
        Index("ix_transacciones_estado_verificacion", "estado_verificacion"),
        Index("ix_transacciones_importacion_id", "importacion_id"),
        Index("idx_transacciones_import_hash", "usuario_id", "import_hash", unique=True, postgresql_where=text("import_hash IS NOT NULL")),
        Index("ix_transacciones_recurrente_fecha", "recurrente_id", "fecha", postgresql_where=text("recurrente_id IS NOT NULL")),
        Index("ix_transacciones_pago_resumen_vencimiento", "tarjeta_id", "pago_resumen_vencimiento", postgresql_where=text("pago_resumen_vencimiento IS NOT NULL")),
        Index("ix_transacciones_pago_origen_id", "pago_origen_id", postgresql_where=text("pago_origen_id IS NOT NULL")),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    usuario_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False
    )
    tipo: Mapped[TipoTransaccion] = mapped_column(
        SAEnum(TipoTransaccion, values_callable=lambda obj: [e.value for e in obj], name="tipo_transaccion_enum"), nullable=False
    )
    monto: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    moneda: Mapped[Moneda] = mapped_column(SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    descripcion: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    categoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("categorias.id"), nullable=True
    )
    subcategoria_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("subcategorias.id"), nullable=True
    )
    metodo_pago: Mapped[MetodoPago | None] = mapped_column(
        SAEnum(MetodoPago, values_callable=lambda obj: [e.value for e in obj], name="metodo_pago_enum"), nullable=True
    )
    billetera_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("billeteras.id"), nullable=False
    )
    tarjeta_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tarjetas_credito.id"), nullable=True
    )
    primer_vencimiento_manual: Mapped[date | None] = mapped_column(Date, nullable=True)
    es_recurrente: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recurrente_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones_recurrentes.id"), nullable=True
    )
    es_cuota_hija: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Estas transacciones se excluyen de dashboard/graficos en la capa de consulta.
    es_padre_cuotas: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    grupo_cuotas_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("grupos_cuotas.id"), nullable=True
    )
    origen: Mapped[OrigenTransaccion] = mapped_column(
        SAEnum(OrigenTransaccion, values_callable=lambda obj: [e.value for e in obj], name="origen_transaccion_enum"), nullable=False
    )
    estado_verificacion: Mapped[EstadoVerificacionTransaccion | None] = mapped_column(
        SAEnum(EstadoVerificacionTransaccion, values_callable=lambda obj: [e.value for e in obj], name="estado_verificacion_transaccion_enum"),
        nullable=True,
    )
    import_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    importacion_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("importaciones_resumen.id"), nullable=True)
    titular_pdf: Mapped[str | None] = mapped_column(String(150), nullable=True)

    # Vínculo exacto de pago de resumen de tarjeta de crédito (Etapa 3A - Anti doble débito y Reversión)
    pago_resumen_vencimiento: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Campos de trazabilidad de conversión multimoneda (Etapa 2 - Infraestructura)
    monto_original: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    moneda_original: Mapped[Moneda | None] = mapped_column(
        SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=True
    )
    cotizacion_aplicada: Mapped[Decimal | None] = mapped_column(Numeric(15, 4), nullable=True)
    tipo_dolar_usado: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Vínculo a la transacción de pago que originó este egreso (ej. percepción impositiva) (Etapa 3C)
    pago_origen_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transacciones.id", ondelete="CASCADE"), nullable=True
    )

    fecha_creacion: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    usuario: Mapped[Usuario] = relationship("Usuario")
    categoria: Mapped[Categoria | None] = relationship("Categoria")
    subcategoria: Mapped[Subcategoria | None] = relationship("Subcategoria")
    billetera: Mapped[Billetera] = relationship("Billetera")
    tarjeta: Mapped[TarjetaCredito | None] = relationship("TarjetaCredito")
    importacion: Mapped[ImportacionResumen | None] = relationship("ImportacionResumen", back_populates="transacciones")

    def __repr__(self) -> str:
        return (
            "Transaccion("
            f"id={self.id!r}, "
            f"usuario_id={self.usuario_id!r}, "
            f"tipo={self.tipo.value!r}, "
            f"monto={self.monto!r}, "
            f"fecha={self.fecha!r}, "
            f"origen={self.origen.value!r}"
            ")"
        )
