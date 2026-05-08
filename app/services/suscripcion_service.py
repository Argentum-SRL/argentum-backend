from datetime import date
from decimal import Decimal
from uuid import UUID
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from fastapi import HTTPException
from dateutil.relativedelta import relativedelta

from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.schemas.suscripcion import SuscripcionCreate, SuscripcionUpdate, ActualizarPrecioRequest, SuscripcionResponse

DIVISORES = {
    'mensual':    1,
    'bimestral':  2,
    'trimestral': 3,
    'semestral':  6,
    'anual':      12,
}

MESES_FRECUENCIA = {
    'mensual':    1,
    'bimestral':  2,
    'trimestral': 3,
    'semestral':  6,
    'anual':      12,
}

def calcular_costo_mensual(frecuencia: str, monto: Decimal) -> Decimal:
    divisor = DIVISORES.get(frecuencia, 1)
    return round(monto / Decimal(divisor), 2)

def calcular_siguiente_cobro(fecha_actual: date, frecuencia: str) -> date:
    meses = MESES_FRECUENCIA.get(frecuencia, 1)
    return fecha_actual + relativedelta(months=meses)

def obtener_precio_vigente(
    db: Session,
    suscripcion_id: UUID,
    fecha: date | None = None
) -> HistorialSuscripcion | None:
    if fecha is None:
        fecha = date.today()
    
    # Buscamos el precio cuya fecha de vigencia sea <= a la fecha consultada, 
    # ordenando por vigente_desde DESC y fecha_creacion DESC como desempate.
    return (
        db.query(HistorialSuscripcion)
        .filter(
            HistorialSuscripcion.suscripcion_id == suscripcion_id,
            HistorialSuscripcion.vigente_desde <= fecha
        )
        .order_by(HistorialSuscripcion.vigente_desde.desc(), HistorialSuscripcion.fecha_creacion.desc())
        .first()
    )

def crear_suscripcion(db: Session, usuario_id: UUID, data: SuscripcionCreate) -> Suscripcion:
    # 1. Validar exclusividad y pertenencia de billetera/tarjeta
    if data.billetera_id and data.tarjeta_id:
        raise HTTPException(status_code=400, detail="Una suscripción no puede estar vinculada a una billetera y a una tarjeta al mismo tiempo.")

    if data.billetera_id:
        bill = db.query(Billetera).filter(Billetera.id == data.billetera_id, Billetera.usuario_id == usuario_id).first()
        if not bill:
            raise HTTPException(status_code=404, detail="Billetera no encontrada")
    
    if data.tarjeta_id:
        tarjeta = db.query(TarjetaCredito).filter(TarjetaCredito.id == data.tarjeta_id, TarjetaCredito.usuario_id == usuario_id).first()
        if not tarjeta:
            raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    # 2. Crear suscripción
    nueva_suscripcion = Suscripcion(
        usuario_id=usuario_id,
        nombre=data.nombre,
        categoria_id=data.categoria_id,
        frecuencia=FrecuenciaSuscripcion(data.frecuencia),
        proximo_cobro=data.proximo_cobro,
        billetera_id=data.billetera_id,
        tarjeta_id=data.tarjeta_id,
        estado=EstadoSuscripcion.ACTIVA
    )
    db.add(nueva_suscripcion)
    db.flush() # Para tener el ID

    # 3. Crear primer registro de historial
    primer_precio = HistorialSuscripcion(
        suscripcion_id=nueva_suscripcion.id,
        monto=data.monto,
        moneda=data.moneda,
        vigente_desde=data.vigente_desde or date.today()
    )
    db.add(primer_precio)
    db.commit()
    db.refresh(nueva_suscripcion)
    return nueva_suscripcion

def obtener_suscripciones(db: Session, usuario_id: UUID, estado: str | None = None) -> List[SuscripcionResponse]:
    query = db.query(Suscripcion).filter(Suscripcion.usuario_id == usuario_id)
    if estado:
        query = query.filter(Suscripcion.estado == EstadoSuscripcion(estado))
    
    suscripciones = query.order_by(Suscripcion.fecha_creacion.desc()).all()
    
    res = []
    for s in suscripciones:
        precio = obtener_precio_vigente(db, s.id)
        costo_mensual = calcular_costo_mensual(s.frecuencia.value, precio.monto) if precio else None
        
        historial = sorted(s.historial, key=lambda x: x.vigente_desde, reverse=True)
        
        s_data = SuscripcionResponse.model_validate(s)
        s_data.precio_actual = precio
        s_data.costo_mensual_equivalente = costo_mensual
        s_data.historial_precios = historial
        res.append(s_data)
        
    return res

def obtener_suscripcion_detalle(db: Session, usuario_id: UUID, suscripcion_id: UUID) -> SuscripcionResponse:
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="Suscripción no encontrada")
    
    precio = obtener_precio_vigente(db, suscripcion.id)
    costo_mensual = calcular_costo_mensual(suscripcion.frecuencia.value, precio.monto) if precio else None
    historial = sorted(suscripcion.historial, key=lambda x: x.vigente_desde, reverse=True)
    
    s_data = SuscripcionResponse.model_validate(suscripcion)
    s_data.precio_actual = precio
    s_data.costo_mensual_equivalente = costo_mensual
    s_data.historial_precios = historial
    return s_data

def actualizar_suscripcion(db: Session, usuario_id: UUID, suscripcion_id: UUID, data: SuscripcionUpdate) -> Suscripcion:
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="Suscripción no encontrada")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key == 'estado':
            setattr(suscripcion, key, EstadoSuscripcion(value))
        elif key == 'frecuencia':
            setattr(suscripcion, key, FrecuenciaSuscripcion(value))
        else:
            setattr(suscripcion, key, value)

    db.commit()
    db.refresh(suscripcion)
    return suscripcion

def actualizar_precio(db: Session, usuario_id: UUID, suscripcion_id: UUID, data: ActualizarPrecioRequest) -> HistorialSuscripcion:
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="Suscripción no encontrada")

    nuevo_precio = HistorialSuscripcion(
        suscripcion_id=suscripcion_id,
        monto=data.monto,
        moneda=data.moneda,
        vigente_desde=data.vigente_desde
    )
    db.add(nuevo_precio)
    db.commit()
    db.refresh(nuevo_precio)
    return nuevo_precio

def cambiar_estado(db: Session, usuario_id: UUID, suscripcion_id: UUID, nuevo_estado: EstadoSuscripcion) -> Suscripcion:
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="Suscripción no encontrada")
    
    if suscripcion.estado == EstadoSuscripcion.CANCELADA and nuevo_estado == EstadoSuscripcion.ACTIVA:
        raise HTTPException(status_code=400, detail="No se puede reactivar una suscripción cancelada. Creá una nueva.")
    
    if suscripcion.estado == EstadoSuscripcion.CANCELADA and nuevo_estado == EstadoSuscripcion.CANCELADA:
        raise HTTPException(status_code=400, detail="Esta suscripción ya está cancelada.")

    suscripcion.estado = nuevo_estado
    db.commit()
    db.refresh(suscripcion)
    return suscripcion

def eliminar_suscripcion(db: Session, usuario_id: UUID, suscripcion_id: UUID):
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="Suscripción no encontrada")
    
    if suscripcion.estado != EstadoSuscripcion.CANCELADA:
        raise HTTPException(status_code=400, detail="Cancelá la suscripción antes de eliminarla.")

    db.delete(suscripcion)
    db.commit()

def obtener_total_mensual(db: Session, usuario_id: UUID) -> dict:
    suscripciones_activas = obtener_suscripciones(db, usuario_id, estado='activa')
    total_ars = sum(
        s.costo_mensual_equivalente
        for s in suscripciones_activas
        if s.precio_actual and s.precio_actual.moneda == 'ARS' and s.costo_mensual_equivalente
    )
    total_usd = sum(
        s.costo_mensual_equivalente
        for s in suscripciones_activas
        if s.precio_actual and s.precio_actual.moneda == 'USD' and s.costo_mensual_equivalente
    )
    return { "total_ars": total_ars, "total_usd": total_usd }
