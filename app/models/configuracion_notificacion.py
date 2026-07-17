import uuid
from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class ConfiguracionNotificacion(Base):
    __tablename__ = "configuracion_notificaciones"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    usuario_id = Column(UUID(as_uuid=True), ForeignKey("usuarios.id"), nullable=False, unique=True)

    # --- CUOTAS ---
    cuota_vence_anticipacion_dias = Column(Integer, default=3, nullable=False)
    cuota_vence_web = Column(Boolean, default=True, nullable=False)
    cuota_vence_whatsapp = Column(Boolean, default=True, nullable=False)

    # --- PRESUPUESTOS: Umbral configurable (default 80%) ---
    presupuesto_umbral_1 = Column(Integer, default=80, nullable=False)
    presupuesto_umbral_1_activo = Column(Boolean, default=True, nullable=False)
    presupuesto_umbral_1_web = Column(Boolean, default=True, nullable=False)
    presupuesto_umbral_1_whatsapp = Column(Boolean, default=False, nullable=False)

    # --- PRESUPUESTOS: 100% (no desactivable, solo canal) ---
    presupuesto_umbral_2_web = Column(Boolean, default=True, nullable=False)
    presupuesto_umbral_2_whatsapp = Column(Boolean, default=True, nullable=False)

    # --- SUSCRIPCIONES: cobro del día (no desactivable, solo canal) ---
    suscripcion_hoy_web = Column(Boolean, default=True, nullable=False)
    suscripcion_hoy_whatsapp = Column(Boolean, default=True, nullable=False)

    # --- SUSCRIPCIONES: recordatorio anticipado ---
    suscripcion_recordatorio_activo = Column(Boolean, default=True, nullable=False)
    suscripcion_recordatorio_dias = Column(Integer, default=3, nullable=False)
    suscripcion_recordatorio_web = Column(Boolean, default=True, nullable=False)
    suscripcion_recordatorio_whatsapp = Column(Boolean, default=False, nullable=False)

    # --- METAS ---
    meta_alcanzada_activo = Column(Boolean, default=True, nullable=False)
    meta_alcanzada_web = Column(Boolean, default=True, nullable=False)
    meta_alcanzada_whatsapp = Column(Boolean, default=True, nullable=False)

    # --- SALDO EN CERO ---
    saldo_cero_activo = Column(Boolean, default=True, nullable=False)
    saldo_cero_web = Column(Boolean, default=True, nullable=False)
    saldo_cero_whatsapp = Column(Boolean, default=True, nullable=False)

    # --- GASTOS INUSUALES ---
    gasto_inusual_activo = Column(Boolean, default=True, nullable=False)
    gasto_inusual_web = Column(Boolean, default=True, nullable=False)
    gasto_inusual_whatsapp = Column(Boolean, default=False, nullable=False)

    # --- RESUMEN SEMANAL (SOFT) ---
    resumen_semanal_activo = Column(Boolean, default=False, nullable=False)
    resumen_semanal_web = Column(Boolean, default=True, nullable=False)
    resumen_semanal_whatsapp = Column(Boolean, default=False, nullable=False)

    # --- INACTIVIDAD (SOFT) ---
    inactividad_activo = Column(Boolean, default=False, nullable=False)
    inactividad_dias = Column(Integer, default=7, nullable=False)
    inactividad_web = Column(Boolean, default=True, nullable=False)
    inactividad_whatsapp = Column(Boolean, default=False, nullable=False)

    # --- RESUMEN DE CICLO ---
    resumen_ciclo_activo = Column(Boolean, default=True, nullable=False)
    resumen_ciclo_web = Column(Boolean, default=False, nullable=False)
    resumen_ciclo_whatsapp = Column(Boolean, default=True, nullable=False)

    # --- PROYECCION NEGATIVA ---
    proyeccion_negativa_activo = Column(Boolean, default=True, nullable=False)
    proyeccion_negativa_web = Column(Boolean, default=True, nullable=False)
    proyeccion_negativa_whatsapp = Column(Boolean, default=True, nullable=False)

    # --- WHATSAPP: horario de envío ---
    whatsapp_hora_envio = Column(Integer, default=9, nullable=False)
    whatsapp_minuto_envio = Column(Integer, default=0, nullable=False)

    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relación
    usuario = relationship("Usuario", back_populates="configuracion_notificacion")

    __table_args__ = (
        # Índice para el job de WhatsApp batched que filtra por hora configurada
        Index(
            "ix_config_notif_whatsapp_hora",
            "whatsapp_hora_envio"
        ),
    )
