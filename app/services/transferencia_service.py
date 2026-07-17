import logging
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.transferencia_interna import TransferenciaInterna
from app.schemas.transferencia_interna import TransferenciaInternaCreate

logger = logging.getLogger(__name__)


def obtener_transferencias(db: Session, usuario_id: UUID):
    return db.execute(
        select(TransferenciaInterna)
        .where(TransferenciaInterna.usuario_id == usuario_id)
        .order_by(desc(TransferenciaInterna.fecha), desc(TransferenciaInterna.fecha_creacion))
    ).scalars().all()


def obtener_transferencia(db: Session, usuario_id: UUID, transferencia_id: UUID) -> TransferenciaInterna:
    tr = db.execute(
        select(TransferenciaInterna).where(
            TransferenciaInterna.id == transferencia_id, 
            TransferenciaInterna.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not tr:
        raise HTTPException(status_code=404, detail="Transferencia no encontrada")
    return tr


def crear_transferencia(db: Session, usuario_id: UUID, data: TransferenciaInternaCreate) -> TransferenciaInterna:
    if data.billetera_origen_id == data.billetera_destino_id:
        raise HTTPException(status_code=400, detail="La billetera de origen y destino no pueden ser la misma.")

    # 1. Validar billeteras
    b_origen = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_origen_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()
    
    b_destino = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_destino_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not b_origen:
        raise HTTPException(status_code=404, detail="No encontramos la billetera de origen.")
    if not b_destino:
        raise HTTPException(status_code=404, detail="No encontramos la billetera de destino.")

    from app.services.transaccion_service import _validar_moneda_coincide
    _validar_moneda_coincide(data.moneda, b_origen)
    if b_origen.moneda != b_destino.moneda:
        raise HTTPException(
            status_code=400,
            detail="No se permiten transferencias entre billeteras de distinta moneda."
        )
    _validar_moneda_coincide(data.moneda, b_destino)

    # 2. Crear registro
    nueva_tr = TransferenciaInterna(
        **data.model_dump(exclude={"usuario_id"}),
        usuario_id=usuario_id
    )

    # 3. Impactar saldos
    # Solo se permiten transferencias de la misma moneda.
    b_origen.saldo_actual -= data.monto
    if b_origen.saldo_actual <= 0:
        try:
            from app.services.notificacion_service import obtener_configuracion, resolver_canales_notificacion, crear_notificacion
            from app.models.notificacion import TipoNotificacion, NivelNotificacion
            config = obtener_configuracion(db, usuario_id)
            canales = resolver_canales_notificacion(config, TipoNotificacion.SALDO_CERO)
            if canales is not None:
                canal_web, canal_whatsapp = canales
                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.SALDO_CERO,
                    nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                    mensaje=f"Tu billetera '{b_origen.nombre}' quedó sin saldo disponible.",
                    entidad_tipo="billetera",
                    entidad_id=b_origen.id,
                    deep_link="/app/billeteras",
                    canal_web=canal_web,
                    canal_whatsapp=canal_whatsapp,
                )
        except Exception:
            pass
    b_destino.saldo_actual += data.monto

    db.add(nueva_tr)
    db.commit()
    db.refresh(nueva_tr)
    return nueva_tr


def eliminar_transferencia(db: Session, usuario_id: UUID, transferencia_id: UUID):
    tr = obtener_transferencia(db, usuario_id, transferencia_id)

    # Revertir impactos
    b_origen = db.get(Billetera, tr.billetera_origen_id)
    b_destino = db.get(Billetera, tr.billetera_destino_id)

    if b_origen:
        try:
            from app.services.transaccion_service import _validar_moneda_coincide
            _validar_moneda_coincide(tr.moneda, b_origen)
            b_origen.saldo_actual += tr.monto
        except Exception as e:
            logger.critical(f"Error crítico de inconsistencia de moneda al revertir origen de transferencia {tr.id}: {e}")
    if b_destino:
        try:
            from app.services.transaccion_service import _validar_moneda_coincide
            _validar_moneda_coincide(tr.moneda, b_destino)
            b_destino.saldo_actual -= tr.monto
        except Exception as e:
            logger.critical(f"Error crítico de inconsistencia de moneda al revertir destino de transferencia {tr.id}: {e}")

    db.delete(tr)
    db.commit()
    return {"detail": "Transferencia eliminada exitosamente"}
