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
