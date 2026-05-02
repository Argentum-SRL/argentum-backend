from uuid import UUID
from datetime import date
from decimal import Decimal
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import select, desc, or_, delete
from sqlalchemy.orm import Session
from dateutil.relativedelta import relativedelta

from app.models.transaccion import Transaccion, TipoTransaccion, OrigenTransaccion, EstadoVerificacionTransaccion
from app.models.billetera import Billetera
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate


def obtener_transacciones(
    db: Session, 
    usuario_id: UUID, 
    skip: int = 0, 
    limit: int = 100,
    billetera_id: Optional[UUID] = None,
    tipo: Optional[TipoTransaccion] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    categoria_id: Optional[UUID] = None,
    subcategoria_id: Optional[UUID] = None,
    moneda: Optional[str] = None,
    estado_verificacion: Optional[str] = None,
    busqueda: Optional[str] = None,
    es_cuota_hija: Optional[bool] = None
):
    # El usuario solo ve transacciones normales e hijas. Nunca las "padre de cuotas".
    query = select(Transaccion).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.es_padre_cuotas == False
    )
    
    if billetera_id:
        query = query.where(Transaccion.billetera_id == billetera_id)
    if tipo:
        query = query.where(Transaccion.tipo == tipo)
    if fecha_desde:
        query = query.where(Transaccion.fecha >= fecha_desde)
    if fecha_hasta:
        query = query.where(Transaccion.fecha <= fecha_hasta)
    if categoria_id:
        query = query.where(Transaccion.categoria_id == categoria_id)
    if subcategoria_id:
        query = query.where(Transaccion.subcategoria_id == subcategoria_id)
    if moneda:
        query = query.where(Transaccion.moneda == moneda)
    if estado_verificacion:
        query = query.where(Transaccion.estado_verificacion == estado_verificacion)
    if busqueda:
        query = query.where(Transaccion.descripcion.ilike(f"%{busqueda}%"))
    if es_cuota_hija is not None:
        query = query.where(Transaccion.es_cuota_hija == es_cuota_hija)
        
    query = query.order_by(desc(Transaccion.fecha), desc(Transaccion.fecha_creacion))
    
    return db.execute(query.offset(skip).limit(limit)).scalars().all()


def obtener_transaccion(db: Session, usuario_id: UUID, transaccion_id: UUID) -> Transaccion:
    transaccion = db.execute(
        select(Transaccion).where(
            Transaccion.id == transaccion_id, 
            Transaccion.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not transaccion:
        raise HTTPException(status_code=404, detail="Transacción no encontrada")
    return transaccion


def crear_transaccion(db: Session, usuario_id: UUID, data: TransaccionCreate) -> Transaccion:
    # 1. Validar billetera
    billetera = db.execute(
        select(Billetera).where(
            Billetera.id == data.billetera_id, 
            Billetera.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not billetera:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")
    
    # 2. Manejo de Cuotas
    if data.es_padre_cuotas:
        if not data.info_cuotas:
            raise HTTPException(status_code=400, detail="Debe proporcionar info_cuotas si es padre de cuotas")
        
        # Crear transaccion padre (no impacta saldo)
        nueva_transaccion = Transaccion(
            **data.model_dump(exclude={"usuario_id", "info_cuotas"}),
            usuario_id=usuario_id,
            monto=data.info_cuotas.monto_total # Guardamos el total en el padre para registro
        )
        db.add(nueva_transaccion)
        db.flush()

        # Calculo Amortizacion Francesa
        monto_total = data.info_cuotas.monto_total
        cant = data.info_cuotas.cantidad_cuotas
        
        if data.info_cuotas.tiene_interes and data.info_cuotas.tasa_interes:
            tasa_mensual = data.info_cuotas.tasa_interes / 100
            if tasa_mensual > 0:
                monto_cuota = monto_total * (tasa_mensual * (1 + tasa_mensual)**cant) / ((1 + tasa_mensual)**cant - 1)
            else:
                monto_cuota = monto_total / cant
        else:
            monto_cuota = monto_total / cant

        total_financiado = monto_cuota * cant

        grupo = GrupoCuotas(
            usuario_id=usuario_id,
            transaccion_padre_id=nueva_transaccion.id,
            descripcion=data.descripcion,
            monto_total=monto_total,
            cantidad_cuotas=cant,
            tiene_interes=data.info_cuotas.tiene_interes,
            tasa_interes=data.info_cuotas.tasa_interes,
            total_financiado=total_financiado,
            moneda=data.moneda
        )
        db.add(grupo)
        db.flush()

        # Generar cuotas (empiezan el mes SIGUIENTE)
        for i in range(1, cant + 1):
            fecha_cuota = data.fecha + relativedelta(months=i)
            
            hija = Transaccion(
                usuario_id=usuario_id,
                tipo=data.tipo,
                monto=monto_cuota,
                moneda=data.moneda,
                fecha=fecha_cuota,
                descripcion=f"{data.descripcion} (Cuota {i}/{cant})",
                categoria_id=data.categoria_id,
                subcategoria_id=data.subcategoria_id,
                metodo_pago=data.metodo_pago,
                billetera_id=data.billetera_id,
                es_cuota_hija=True,
                grupo_cuotas_id=grupo.id,
                origen=data.origen
            )
            db.add(hija)
            db.flush()

            cuota_reg = Cuota(
                grupo_id=grupo.id,
                transaccion_id=hija.id,
                numero_cuota=i,
                monto_proyectado=monto_cuota,
                fecha_vencimiento=fecha_cuota,
                pagada=False # Las cuotas futuras no se pagan solas al crear el grupo
            )
            db.add(cuota_reg)

            # Al crear un grupo de cuotas, NINGUNA impacta el saldo hoy
            # porque la primera empieza el mes que viene.
        
        db.commit()
        db.refresh(nueva_transaccion)
        return nueva_transaccion

    # 3. Transacción normal
    nueva_transaccion = Transaccion(
        **data.model_dump(exclude={"usuario_id", "info_cuotas"}),
        usuario_id=usuario_id
    )
    
    # 4. Actualizar saldo solo si es confirmada y es hoy o pasada
    if (nueva_transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE 
        and nueva_transaccion.fecha <= date.today()):
        if nueva_transaccion.tipo == TipoTransaccion.INGRESO:
            billetera.saldo_actual += nueva_transaccion.monto
        else:
            billetera.saldo_actual -= nueva_transaccion.monto
        
    db.add(nueva_transaccion)
    db.commit()
    db.refresh(nueva_transaccion)
    return nueva_transaccion


def actualizar_transaccion(db: Session, usuario_id: UUID, transaccion_id: UUID, data: TransaccionUpdate) -> Transaccion:
    transaccion = obtener_transaccion(db, usuario_id, transaccion_id)
    
    if transaccion.es_cuota_hija or transaccion.es_padre_cuotas:
        raise HTTPException(status_code=400, detail="No se pueden editar transacciones ligadas a cuotas individualmente.")

    impacto_saldo_cambia = any([
        data.monto is not None,
        data.tipo is not None,
        data.billetera_id is not None and data.billetera_id != transaccion.billetera_id,
        data.fecha is not None,
        data.estado_verificacion is not None
    ])
    
    if impacto_saldo_cambia:
        # Revertir impacto anterior si existia
        if (transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE 
            and transaccion.fecha <= date.today()):
            billetera_vieja = db.get(Billetera, transaccion.billetera_id)
            if transaccion.tipo == TipoTransaccion.INGRESO:
                billetera_vieja.saldo_actual -= transaccion.monto
            else:
                billetera_vieja.saldo_actual += transaccion.monto
            
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(transaccion, key, value)
            
        # Aplicar nuevo impacto
        if (transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE 
            and transaccion.fecha <= date.today()):
            billetera_nueva = db.get(Billetera, transaccion.billetera_id)
            if not billetera_nueva or billetera_nueva.usuario_id != usuario_id:
                raise HTTPException(status_code=404, detail="Billetera no encontrada")
                
            if transaccion.tipo == TipoTransaccion.INGRESO:
                billetera_nueva.saldo_actual += transaccion.monto
            else:
                billetera_nueva.saldo_actual -= transaccion.monto
    else:
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(transaccion, key, value)
            
    db.commit()
    db.refresh(transaccion)
    return transaccion


def eliminar_transaccion(db: Session, usuario_id: UUID, transaccion_id: UUID):
    transaccion = obtener_transaccion(db, usuario_id, transaccion_id)
    
    # Manejo de cascada para cuotas
    if transaccion.es_padre_cuotas or transaccion.es_cuota_hija:
        # 1. Identificar el grupo
        if transaccion.es_padre_cuotas:
            grupo = db.execute(select(GrupoCuotas).where(GrupoCuotas.transaccion_padre_id == transaccion.id)).scalar_one_or_none()
        else:
            grupo = db.get(GrupoCuotas, transaccion.grupo_cuotas_id)
        
        if grupo:
            # 2. Revertir saldo de cuotas ya pagadas
            cuotas = db.execute(select(Cuota).where(Cuota.grupo_id == grupo.id)).scalars().all()
            for c in cuotas:
                if c.pagada or c.fecha_vencimiento <= date.today():
                    tx_hija = db.get(Transaccion, c.transaccion_id)
                    if tx_hija:
                        b = db.get(Billetera, tx_hija.billetera_id)
                        if b:
                            if tx_hija.tipo == TipoTransaccion.INGRESO:
                                b.saldo_actual -= tx_hija.monto
                            else:
                                b.saldo_actual += tx_hija.monto
            
            # 3. Eliminar todo en orden
            id_hijas = [c.transaccion_id for c in cuotas]
            id_padre = grupo.transaccion_padre_id
            
            db.execute(delete(Cuota).where(Cuota.grupo_id == grupo.id))
            db.execute(delete(Transaccion).where(Transaccion.id.in_(id_hijas)))
            db.execute(delete(GrupoCuotas).where(GrupoCuotas.id == grupo.id))
            db.execute(delete(Transaccion).where(Transaccion.id == id_padre))
            db.commit()
            return {"detail": "Grupo de cuotas eliminado exitosamente"}

    # Transaccion normal
    if (transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE 
        and transaccion.fecha <= date.today()):
        billetera = db.get(Billetera, transaccion.billetera_id)
        if billetera:
            if transaccion.tipo == TipoTransaccion.INGRESO:
                billetera.saldo_actual -= transaccion.monto
            else:
                billetera.saldo_actual += transaccion.monto
            
    db.delete(transaccion)
    db.commit()
    return {"detail": "Transacción eliminada exitosamente"}


def confirmar_transaccion_ia(db: Session, usuario_id: UUID, transaccion_id: UUID) -> Transaccion:
    transaccion = obtener_transaccion(db, usuario_id, transaccion_id)
    
    if transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE:
        raise HTTPException(status_code=400, detail="La transacción ya está confirmada o no requiere verificación.")
        
    transaccion.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
    
    # Al confirmar, RECIEN impacta el saldo si la fecha es hoy o pasada
    if transaccion.fecha <= date.today():
        billetera = db.get(Billetera, transaccion.billetera_id)
        if not billetera:
            raise HTTPException(status_code=404, detail="Billetera no encontrada")
            
        if transaccion.tipo == TipoTransaccion.INGRESO:
            billetera.saldo_actual += transaccion.monto
        else:
            billetera.saldo_actual -= transaccion.monto
            
    db.commit()
    db.refresh(transaccion)
    return transaccion


def obtener_pendientes_ia(db: Session, usuario_id: UUID):
    return db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
        ).order_by(desc(Transaccion.fecha))
    ).scalars().all()
