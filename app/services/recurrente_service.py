import calendar
from uuid import UUID
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from app.models.transaccion import Transaccion, OrigenTransaccion
from app.models.billetera import Billetera
from app.models.transaccion_recurrente import TransaccionRecurrente, EstadoTransaccionRecurrente
from app.schemas.transaccion_recurrente import TransaccionRecurrenteCreate, TransaccionRecurrenteUpdate


def obtener_recurrentes(db: Session, usuario_id: UUID):
    return db.execute(
        select(TransaccionRecurrente)
        .where(TransaccionRecurrente.usuario_id == usuario_id)
        .order_by(desc(TransaccionRecurrente.fecha_creacion))
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
    Genera transacciones reales a partir de las plantillas recurrentes.
    """
    hoy = date.today()
    ultimo_dia_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    
    recurrentes = db.execute(
        select(TransaccionRecurrente).where(TransaccionRecurrente.estado == EstadoTransaccionRecurrente.ACTIVA)
    ).scalars().all()

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
            # Primera quincena
            target_day_1 = min(rec.dia_registro, ultimo_dia_mes)
            # Segunda quincena (+15 días)
            target_day_2 = min(rec.dia_registro + 15, ultimo_dia_mes)
            
            if hoy.day == target_day_1 or hoy.day == target_day_2:
                debe_generar = True

        if debe_generar:
            # Verificar duplicados para hoy
            ya_existe = db.execute(
                select(Transaccion).where(
                    Transaccion.recurrente_id == rec.id,
                    Transaccion.fecha == hoy
                )
            ).scalar_one_or_none()

            if not ya_existe:
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
                db.add(nueva_tx)
                
                # Impactar saldo
                billetera = db.get(Billetera, rec.billetera_id)
                if billetera:
                    if rec.tipo == "ingreso":
                        billetera.saldo_actual += rec.monto
                    else:
                        billetera.saldo_actual -= rec.monto
                
                generadas += 1

    db.commit()
    return generadas
