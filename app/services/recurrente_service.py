import calendar
import logging
from uuid import UUID
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select, desc, or_
from sqlalchemy.orm import Session

from app.models.transaccion import Transaccion, OrigenTransaccion
from app.models.billetera import Billetera
from app.models.transaccion_recurrente import TransaccionRecurrente, EstadoTransaccionRecurrente
from app.schemas.transaccion_recurrente import TransaccionRecurrenteCreate, TransaccionRecurrenteUpdate
from app.services import presupuesto_service
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)


def obtener_recurrentes(db: Session, usuario_id: UUID, skip: int = 0, limit: int = 50):
    return db.execute(
        select(TransaccionRecurrente)
        .where(TransaccionRecurrente.usuario_id == usuario_id)
        .order_by(desc(TransaccionRecurrente.fecha_creacion))
        .offset(skip)
        .limit(limit)
    ).scalars().all()


def obtener_recurrente(db: Session, usuario_id: UUID, recurrente_id: UUID) -> TransaccionRecurrente:
    rec = db.execute(
        select(TransaccionRecurrente).where(
            TransaccionRecurrente.id == recurrente_id, 
            TransaccionRecurrente.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not rec:
        raise HTTPException(status_code=404, detail="Transacción recurrente no encontrada")
    return rec


def crear_recurrente(db: Session, usuario_id: UUID, data: TransaccionRecurrenteCreate) -> TransaccionRecurrente:
    # Validar billetera
    billetera = db.execute(
        select(Billetera).where(Billetera.id == data.billetera_id, Billetera.usuario_id == usuario_id)
    ).scalar_one_or_none()
    
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")

    # Validar categoría
    if not data.categoria_id:
        raise HTTPException(status_code=400, detail="Debés seleccionar una categoría.")

    from app.models.categoria import Categoria, EstadoCategoria
    categoria = db.execute(
        select(Categoria).where(
            Categoria.id == data.categoria_id,
            or_(
                Categoria.creador_id == usuario_id,
                Categoria.es_global == True
            ),
            Categoria.estado == EstadoCategoria.ACTIVA
        )
    ).scalar_one_or_none()

    if not categoria:
        raise HTTPException(status_code=404, detail="No encontramos esa categoría.")

    nueva_recurrente = TransaccionRecurrente(
        **data.model_dump(exclude={"usuario_id"}),
        usuario_id=usuario_id
    )
    db.add(nueva_recurrente)
    db.commit()
    db.refresh(nueva_recurrente)
    return nueva_recurrente


def actualizar_recurrente(
    db: Session, 
    usuario_id: UUID, 
    recurrente_id: UUID, 
    data: TransaccionRecurrenteUpdate
) -> TransaccionRecurrente:
    recurrente = obtener_recurrente(db, usuario_id, recurrente_id)

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(recurrente, key, value)

    db.commit()
    db.refresh(recurrente)
    return recurrente


def cambiar_estado_recurrente(db: Session, usuario_id: UUID, recurrente_id: UUID, nuevo_estado: EstadoTransaccionRecurrente) -> TransaccionRecurrente:
    recurrente = obtener_recurrente(db, usuario_id, recurrente_id)
    recurrente.estado = nuevo_estado
    db.commit()
    db.refresh(recurrente)
    return recurrente


def eliminar_recurrente(db: Session, usuario_id: UUID, recurrente_id: UUID):
    recurrente = obtener_recurrente(db, usuario_id, recurrente_id)
    db.delete(recurrente)
    db.commit()
    return {"detail": "Transacción recurrente eliminada exitosamente"}


# --- Background Job ---

def procesar_recurrentes(db: Session):
    """
    Genera transacciones reales a partir de las plantillas recurrentes (Optimizado).
    """
    hoy = hoy_argentina()
    ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    
    recurrentes = db.execute(
        select(TransaccionRecurrente).where(TransaccionRecurrente.estado == EstadoTransaccionRecurrente.ACTIVA)
    ).scalars().all()

    if not recurrentes:
        return 0

    # PRECARGA: Obtener IDs necesarios
    billeteras_ids = {r.billetera_id for r in recurrentes}
    recurrente_ids = {r.id for r in recurrentes}

    # PRECARGA: Billeteras en un mapa para acceso rápido O(1)
    billeteras_db = db.execute(select(Billetera).where(Billetera.id.in_(billeteras_ids))).scalars().all()
    billeteras_map = {b.id: b for b in billeteras_db}

    # PRECARGA: Transacciones existentes hoy en un set para verificación O(1)
    existentes = db.execute(
        select(Transaccion.recurrente_id).where(
            Transaccion.recurrente_id.in_(recurrente_ids),
            Transaccion.fecha == hoy
        )
    ).scalars().all()
    existentes_set = set(existentes)

    nuevas_txs = []
    generadas = 0

    for rec in recurrentes:
        debe_generar = False
        
        # Lógica de fecha según frecuencia
        if rec.frecuencia == "mensual":
            target_day = min(rec.dia_registro, ultimo_dia_mes)
            if hoy.day == target_day:
                debe_generar = True
                
        elif rec.frecuencia == "semanal":
            if hoy.weekday() == rec.dia_registro:
                debe_generar = True
                
        elif rec.frecuencia == "quincenal":
            target_day_1 = min(rec.dia_registro, ultimo_dia_mes)
            target_day_2 = min(rec.dia_registro + 15, ultimo_dia_mes)
            if hoy.day == target_day_1 or hoy.day == target_day_2:
                debe_generar = True

        if debe_generar and rec.id not in existentes_set:
            billetera = billeteras_map.get(rec.billetera_id)
            if billetera:
                try:
                    from app.services.transaccion_service import _validar_moneda_coincide
                    _validar_moneda_coincide(rec.moneda, billetera)
                except Exception as e:
                    logger.error(
                        f"Error de consistencia de moneda al procesar recurrente {rec.id} del usuario {rec.usuario_id}: {e}"
                    )
                    continue

            nueva_tx = Transaccion(
                usuario_id=rec.usuario_id,
                tipo=rec.tipo,
                monto=rec.monto,
                moneda=rec.moneda,
                fecha=hoy,
                descripcion=rec.descripcion,
                categoria_id=rec.categoria_id,
                subcategoria_id=rec.subcategoria_id,
                billetera_id=rec.billetera_id,
                es_recurrente=True,
                recurrente_id=rec.id,
                origen=OrigenTransaccion.RECURRENTE
            )
            nuevas_txs.append(nueva_tx)
            
            # Impactar saldo en memoria (el objeto está trackeado por SQLAlchemy)
            if billetera:
                if rec.tipo == "ingreso":
                    billetera.saldo_actual += rec.monto
                else:
                    billetera.saldo_actual -= rec.monto
            
            generadas += 1
            
            # Impacto en presupuestos
            presupuesto_service.registrar_impacto_presupuesto(db, nueva_tx, revertir=False)

    if nuevas_txs:
        db.add_all(nuevas_txs)
        db.commit()

    return generadas
