from datetime import date, datetime, timedelta
from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from app.models.notificacion import Notificacion, TipoNotificacion, NivelNotificacion
from app.models.configuracion_notificacion import ConfiguracionNotificacion
import logging

logger = logging.getLogger(__name__)


def crear_notificacion(
    db: Session,
    usuario_id: UUID,
    tipo: TipoNotificacion,
    nivel: NivelNotificacion,
    mensaje: str,
    entidad_tipo: Optional[str] = None,
    entidad_id: Optional[UUID] = None,
    deep_link: Optional[str] = None,
    canal_web: bool = True,
    canal_whatsapp: bool = False,
    canal_email: bool = False,
    grupo_agrupacion_override: Optional[str] = None,
) -> Optional[Notificacion]:
    """
    Crea una notificación con deduplicación diaria por grupo_agrupacion.

    Para tipos genéricos: grupo = "{TIPO}_{YYYYMMDD}"
    Para CUOTA_VENCE con entidad_id: grupo = "CUOTA_VENCE_{entidad_id}_{YYYYMMDD}"
    Esto permite múltiples cuotas venciendo el mismo día sin agruparse.

    Si grupo_agrupacion_override se pasa, usa ese valor directamente.
    Retorna None si ya existe una notificación con el mismo grupo hoy.
    """
    hoy_str = date.today().strftime('%Y%m%d')

    if grupo_agrupacion_override:
        grupo = grupo_agrupacion_override
    elif tipo == TipoNotificacion.CUOTA_VENCE and entidad_id:
        grupo = f"CUOTA_VENCE_{entidad_id}_{hoy_str}"
    else:
        grupo = f"{tipo.value}_{hoy_str}"

    # Verificar duplicado del día
    existente = db.query(Notificacion).filter(
        Notificacion.usuario_id == usuario_id,
        Notificacion.grupo_agrupacion == grupo,
    ).first()
    if existente:
        return None

    notif = Notificacion(
        usuario_id=usuario_id,
        tipo=tipo,
        nivel=nivel,
        mensaje=mensaje,
        entidad_tipo=entidad_tipo,
        entidad_id=entidad_id,
        deep_link=deep_link,
        canal_web=canal_web,
        canal_whatsapp=canal_whatsapp,
        canal_email=canal_email,
        grupo_agrupacion=grupo,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    return notif


def obtener_notificaciones(
    db: Session,
    usuario_id: UUID,
    solo_no_leidas: bool = False,
    incluir_archivadas: bool = False,
    limite: int = 50,
    offset: int = 0,
) -> List[Notificacion]:
    query = db.query(Notificacion).filter(Notificacion.usuario_id == usuario_id)

    if not incluir_archivadas:
        query = query.filter(Notificacion.archivada == False)

    if solo_no_leidas:
        query = query.filter(Notificacion.leida == False)

    # Excluir silenciadas activas
    ahora = datetime.utcnow()
    query = query.filter(
        (Notificacion.silenciada_hasta == None) |
        (Notificacion.silenciada_hasta < ahora)
    )

    return query.order_by(Notificacion.created_at.desc()).offset(offset).limit(limite).all()


def contar_no_leidas(db: Session, usuario_id: UUID) -> int:
    ahora = datetime.utcnow()
    return db.query(Notificacion).filter(
        Notificacion.usuario_id == usuario_id,
        Notificacion.leida == False,
        Notificacion.archivada == False,
        (Notificacion.silenciada_hasta == None) |
        (Notificacion.silenciada_hasta < ahora)
    ).count()


def marcar_leida(db: Session, usuario_id: UUID, notificacion_id: UUID) -> Notificacion:
    notif = db.query(Notificacion).filter(
        Notificacion.id == notificacion_id,
        Notificacion.usuario_id == usuario_id,
    ).first()
    if not notif:
        raise ValueError("Notificación no encontrada")
    notif.leida = True
    db.commit()
    db.refresh(notif)
    return notif


def marcar_no_leida(db: Session, usuario_id: UUID, notificacion_id: UUID) -> Notificacion:
    notif = db.query(Notificacion).filter(
        Notificacion.id == notificacion_id,
        Notificacion.usuario_id == usuario_id,
    ).first()
    if not notif:
        raise ValueError("Notificación no encontrada")
    notif.leida = False
    db.commit()
    db.refresh(notif)
    return notif


def archivar_notificacion(db: Session, usuario_id: UUID, notificacion_id: UUID) -> Notificacion:
    notif = db.query(Notificacion).filter(
        Notificacion.id == notificacion_id,
        Notificacion.usuario_id == usuario_id,
    ).first()
    if not notif:
        raise ValueError("Notificación no encontrada")
    notif.archivada = True
    db.commit()
    db.refresh(notif)
    return notif


def silenciar_notificacion(
    db: Session,
    usuario_id: UUID,
    notificacion_id: UUID,
    horas: int = 24,
) -> Notificacion:
    horas = max(1, min(168, horas))  # entre 1 hora y 7 días
    notif = db.query(Notificacion).filter(
        Notificacion.id == notificacion_id,
        Notificacion.usuario_id == usuario_id,
    ).first()
    if not notif:
        raise ValueError("Notificación no encontrada")
    notif.silenciada_hasta = datetime.utcnow() + timedelta(hours=horas)
    db.commit()
    db.refresh(notif)
    return notif


def eliminar_notificacion(db: Session, usuario_id: UUID, notificacion_id: UUID) -> None:
    notif = db.query(Notificacion).filter(
        Notificacion.id == notificacion_id,
        Notificacion.usuario_id == usuario_id,
    ).first()
    if not notif:
        raise ValueError("Notificación no encontrada")
    if notif.nivel == NivelNotificacion.CRITICA:
        raise PermissionError("Las notificaciones críticas no se pueden eliminar")
    db.delete(notif)
    db.commit()


def marcar_todas_leidas(db: Session, usuario_id: UUID) -> None:
    db.query(Notificacion).filter(
        Notificacion.usuario_id == usuario_id,
        Notificacion.leida == False,
        Notificacion.archivada == False,
    ).update({"leida": True})
    db.commit()


def archivar_todas(db: Session, usuario_id: UUID) -> None:
    db.query(Notificacion).filter(
        Notificacion.usuario_id == usuario_id,
        Notificacion.archivada == False,
        Notificacion.nivel != NivelNotificacion.CRITICA,
    ).update({"archivada": True})
    db.commit()


def obtener_configuracion(db: Session, usuario_id: UUID) -> ConfiguracionNotificacion:
    """Obtiene la configuración del usuario. Si no existe, la crea con defaults."""
    config = db.query(ConfiguracionNotificacion).filter(
        ConfiguracionNotificacion.usuario_id == usuario_id
    ).first()
    if not config:
        config = ConfiguracionNotificacion(usuario_id=usuario_id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def actualizar_configuracion(
    db: Session,
    usuario_id: UUID,
    datos: dict,
) -> ConfiguracionNotificacion:
    config = obtener_configuracion(db, usuario_id)

    for campo, valor in datos.items():
        if hasattr(config, campo):
            setattr(config, campo, valor)

    # Validaciones de rango para evitar valores fuera de límites
    config.cuota_vence_anticipacion_dias = max(1, min(30, config.cuota_vence_anticipacion_dias))
    config.presupuesto_umbral_1 = max(50, min(95, config.presupuesto_umbral_1))
    config.suscripcion_recordatorio_dias = max(1, min(14, config.suscripcion_recordatorio_dias))
    config.inactividad_dias = max(3, min(30, config.inactividad_dias))
    config.whatsapp_hora_envio = max(0, min(23, config.whatsapp_hora_envio))
    config.whatsapp_minuto_envio = max(0, min(59, config.whatsapp_minuto_envio))

    db.commit()
    db.refresh(config)
    return config


def resolver_canales_notificacion(
    config: Optional[ConfiguracionNotificacion],
    tipo: TipoNotificacion
) -> Optional[tuple[bool, bool]]:
    """
    Resuelve si una notificación está activa y por qué canales debe enviarse.
    Retorna None si la notificación está desactivada en la configuración.
    De lo contrario, retorna (canal_web, canal_whatsapp).
    """
    if not config:
        return (True, False)

    # 1. Gasto Inusual
    if tipo == TipoNotificacion.GASTO_INUSUAL:
        if not getattr(config, "gasto_inusual_activo", True):
            return None
        return (
            getattr(config, "gasto_inusual_web", True),
            getattr(config, "gasto_inusual_whatsapp", False),
        )

    # 2. Meta Alcanzada
    elif tipo == TipoNotificacion.META_ALCANZADA:
        if not getattr(config, "meta_alcanzada_activo", True):
            return None
        return (
            getattr(config, "meta_alcanzada_web", True),
            getattr(config, "meta_alcanzada_whatsapp", True),
        )

    # 3. Saldo en Cero
    elif tipo == TipoNotificacion.SALDO_CERO:
        if not getattr(config, "saldo_cero_activo", True):
            return None
        return (
            getattr(config, "saldo_cero_web", True),
            getattr(config, "saldo_cero_whatsapp", True),
        )

    # 4. Resumen de Ciclo
    elif tipo == TipoNotificacion.RESUMEN_CICLO:
        if not getattr(config, "resumen_ciclo_activo", True):
            return None
        return (
            getattr(config, "resumen_ciclo_web", False),
            getattr(config, "resumen_ciclo_whatsapp", True),
        )

    # 5. Proyección Negativa
    elif tipo == TipoNotificacion.PROYECCION_NEGATIVA:
        if not getattr(config, "proyeccion_negativa_activo", True):
            return None
        return (
            getattr(config, "proyeccion_negativa_web", True),
            getattr(config, "proyeccion_negativa_whatsapp", True),
        )

    # Otros tipos de notificación (para soporte completo)
    elif tipo == TipoNotificacion.CUOTA_VENCE:
        return (config.cuota_vence_web, config.cuota_vence_whatsapp)

    elif tipo == TipoNotificacion.PRESUPUESTO_AGOTADO:
        return (config.presupuesto_umbral_2_web, config.presupuesto_umbral_2_whatsapp)

    elif tipo == TipoNotificacion.PRESUPUESTO_LIMITE:
        if not config.presupuesto_umbral_1_activo:
            return None
        return (config.presupuesto_umbral_1_web, config.presupuesto_umbral_1_whatsapp)

    elif tipo == TipoNotificacion.SUSCRIPCION_HOY:
        return (config.suscripcion_hoy_web, config.suscripcion_hoy_whatsapp)

    elif tipo == TipoNotificacion.SUSCRIPCION_PROXIMA:
        if not config.suscripcion_recordatorio_activo:
            return None
        return (config.suscripcion_recordatorio_web, config.suscripcion_recordatorio_whatsapp)

    elif tipo == TipoNotificacion.RESUMEN_SEMANAL:
        if not config.resumen_semanal_activo:
            return None
        return (config.resumen_semanal_web, config.resumen_semanal_whatsapp)

    elif tipo == TipoNotificacion.INACTIVIDAD:
        if not config.inactividad_activo:
            return None
        return (config.inactividad_web, config.inactividad_whatsapp)

    return (True, False)

