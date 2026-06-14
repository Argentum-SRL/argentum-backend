import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum as SAEnum, Text, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class NivelNotificacion(str, enum.Enum):
    CRITICA = "CRITICA"
    FINANCIERA_IMPORTANTE = "FINANCIERA_IMPORTANTE"
    FINANCIERA_INFORMATIVA = "FINANCIERA_INFORMATIVA"
    SOFT = "SOFT"


class TipoNotificacion(str, enum.Enum):
    CAMBIO_CONTRASENA = "CAMBIO_CONTRASENA"
    NUEVO_DISPOSITIVO = "NUEVO_DISPOSITIVO"
    CAMBIO_EMAIL = "CAMBIO_EMAIL"
    INTENTOS_LOGIN_FALLIDOS = "INTENTOS_LOGIN_FALLIDOS"
    WHATSAPP_NUEVO_VINCULADO = "WHATSAPP_NUEVO_VINCULADO"
    CUOTA_VENCE = "CUOTA_VENCE"
    PRESUPUESTO_AGOTADO = "PRESUPUESTO_AGOTADO"
    SALDO_CERO = "SALDO_CERO"
    SUSCRIPCION_HOY = "SUSCRIPCION_HOY"
    PRESUPUESTO_LIMITE = "PRESUPUESTO_LIMITE"
    SUSCRIPCION_PROXIMA = "SUSCRIPCION_PROXIMA"
    RESUMEN_SEMANAL = "RESUMEN_SEMANAL"
    META_ALCANZADA = "META_ALCANZADA"
    GASTO_INUSUAL = "GASTO_INUSUAL"
    INACTIVIDAD = "INACTIVIDAD"
    RESUMEN_CICLO = "RESUMEN_CICLO"


class Notificacion(Base):
    __tablename__ = "notificaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False)

    tipo = Column(SAEnum(TipoNotificacion, name="tipo_notificacion_sa_enum"), nullable=False)
    nivel = Column(SAEnum(NivelNotificacion, name="nivel_notificacion_sa_enum"), nullable=False)
    mensaje = Column(Text, nullable=False)

    # Estado de lectura
    leida = Column(Boolean, default=False, nullable=False)
    archivada = Column(Boolean, default=False, nullable=False)

    # Canales por los que debe entregarse esta notificación
    canal_web = Column(Boolean, default=True, nullable=False)
    canal_whatsapp = Column(Boolean, default=False, nullable=False)
    canal_email = Column(Boolean, default=False, nullable=False)

    # Estado de entrega por canal
    enviada_whatsapp = Column(Boolean, default=False, nullable=False)
    enviada_email = Column(Boolean, default=False, nullable=False)

    # Agrupación diaria para evitar duplicados
    # Formato general: "{TIPO}_{YYYYMMDD}"
    # Para cuotas: "CUOTA_VENCE_{cuota_id}_{YYYYMMDD}"
    grupo_agrupacion = Column(String(200), nullable=True)

    # Referencia a la entidad relacionada
    entidad_tipo = Column(String(50), nullable=True)
    # Valores posibles: "cuota" | "tarjeta" | "presupuesto" | "suscripcion" | "meta" | "billetera"
    entidad_id = Column(UUID(as_uuid=True), nullable=True)

    # Deep link para navegar al detalle al hacer click
    deep_link = Column(String(300), nullable=True)

    # Silenciado temporalmente
    silenciada_hasta = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relaciones
    usuario = relationship("Usuario", back_populates="notificaciones")

    # Índices de performance — críticos para la escala
    __table_args__ = (
        # Índice principal: listar notificaciones del usuario, no archivadas, no leídas
        Index(
            "ix_notificaciones_usuario_activas",
            "usuario_id", "leida", "archivada"
        ),
        # Índice para deduplicación diaria (crear_notificacion lo consulta en cada llamada)
        Index(
            "ix_notificaciones_usuario_grupo",
            "usuario_id", "grupo_agrupacion"
        ),
        # Índice para el job de WhatsApp batched (filtra por canal y enviada)
        Index(
            "ix_notificaciones_whatsapp_pendientes",
            "usuario_id", "canal_whatsapp", "enviada_whatsapp"
        ),
        # Índice para contar no leídas (endpoint /contador)
        Index(
            "ix_notificaciones_usuario_no_leidas",
            "usuario_id", "leida", "archivada", "silenciada_hasta"
        ),
    )
