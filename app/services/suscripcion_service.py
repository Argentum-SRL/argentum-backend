from datetime import date
from decimal import Decimal
from uuid import UUID
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from fastapi import HTTPException
from sqlalchemy.orm import selectinload
from dateutil.relativedelta import relativedelta

from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.schemas.suscripcion import SuscripcionCreate, SuscripcionUpdate, ActualizarPrecioRequest, SuscripcionResponse
from app.utils.fecha import hoy_argentina

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
        fecha = hoy_argentina()
    
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
    # 1. Validar nombre y monto
    if not data.nombre or not data.nombre.strip():
        raise HTTPException(status_code=400, detail="El nombre de la suscripción no puede estar vacío.")
    
    if data.monto <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero.")

    # 2. Validar frecuencia
    try:
        frecuencia_enum = FrecuenciaSuscripcion(data.frecuencia)
    except ValueError:
        raise HTTPException(status_code=400, detail="Frecuencia de suscripción no válida.")

    # 3. Validar exclusividad y pertenencia de billetera/tarjeta
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

    # 4. Validar subcategoría (si fue enviada)
    if data.subcategoria_id:
        from app.models.subcategoria import Subcategoria
        sub = db.query(Subcategoria).filter(
            Subcategoria.id == data.subcategoria_id
        ).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Subcategoría no encontrada.")
        if data.categoria_id and sub.categoria_id != data.categoria_id:
            raise HTTPException(status_code=400, detail="La subcategoría seleccionada no pertenece a la categoría.")

    # 5. Crear suscripción
    nueva_suscripcion = Suscripcion(
        usuario_id=usuario_id,
        nombre=data.nombre.strip(),
        categoria_id=data.categoria_id,
        subcategoria_id=data.subcategoria_id,
        frecuencia=frecuencia_enum,
        proximo_cobro=data.proximo_cobro,
        billetera_id=data.billetera_id,
        tarjeta_id=data.tarjeta_id,
        estado=EstadoSuscripcion.ACTIVA
    )
    db.add(nueva_suscripcion)
    db.flush() # Para tener el ID

    # 6. Crear primer registro de historial
    primer_precio = HistorialSuscripcion(
        suscripcion_id=nueva_suscripcion.id,
        monto=data.monto,
        moneda=data.moneda,
        vigente_desde=data.vigente_desde or hoy_argentina()
    )
    db.add(primer_precio)
    db.commit()
    db.refresh(nueva_suscripcion)

    # Disparar el primer cobro inmediatamente sólo si la fecha es hoy o anterior
    if (nueva_suscripcion.billetera_id or nueva_suscripcion.tarjeta_id) and nueva_suscripcion.proximo_cobro <= hoy_argentina():
        from app.services.cobro_suscripcion_service import _cobrar_suscripcion
        from app.services import tarjeta_service

        primer_vencimiento = None
        if nueva_suscripcion.tarjeta_id:
            tarjeta = db.query(TarjetaCredito).filter(
                TarjetaCredito.id == nueva_suscripcion.tarjeta_id
            ).first()
            if tarjeta:
                primer_vencimiento = tarjeta_service.calcular_fecha_vencimiento_proximo(tarjeta)

        _cobrar_suscripcion(db, nueva_suscripcion, nueva_suscripcion.proximo_cobro, primer_vencimiento)
        db.commit()
        db.refresh(nueva_suscripcion)

    return nueva_suscripcion

def obtener_suscripciones(db: Session, usuario_id: UUID, estado: str | None = None) -> List[SuscripcionResponse]:
    query = db.query(Suscripcion).options(
        selectinload(Suscripcion.historial)
    ).filter(Suscripcion.usuario_id == usuario_id)
    if estado:
        query = query.filter(Suscripcion.estado == EstadoSuscripcion(estado))
    
    suscripciones = query.order_by(Suscripcion.fecha_creacion.desc()).all()
    hoy = hoy_argentina()
    
    res = []
    for s in suscripciones:
        historial_ordenado = sorted(
            s.historial, 
            key=lambda x: (x.vigente_desde, x.fecha_creacion), 
            reverse=True
        )
        precios_vigentes = [p for p in historial_ordenado if p.vigente_desde <= hoy]
        precio = precios_vigentes[0] if precios_vigentes else (historial_ordenado[0] if historial_ordenado else None)
        costo_mensual = calcular_costo_mensual(s.frecuencia.value, precio.monto) if precio else None
        
        s_data = SuscripcionResponse.model_validate(s)
        s_data.precio_actual = precio
        s_data.costo_mensual_equivalente = costo_mensual
        s_data.historial_precios = historial_ordenado
        res.append(s_data)
        
    return res

def obtener_suscripcion_detalle(db: Session, usuario_id: UUID, suscripcion_id: UUID) -> SuscripcionResponse:
    suscripcion = db.query(Suscripcion).options(
        selectinload(Suscripcion.historial)
    ).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="No encontramos esa suscripción.")
    
    hoy = hoy_argentina()
    historial_ordenado = sorted(
        suscripcion.historial, 
        key=lambda x: (x.vigente_desde, x.fecha_creacion), 
        reverse=True
    )
    precios_vigentes = [p for p in historial_ordenado if p.vigente_desde <= hoy]
    precio = precios_vigentes[0] if precios_vigentes else (historial_ordenado[0] if historial_ordenado else None)
    costo_mensual = calcular_costo_mensual(suscripcion.frecuencia.value, precio.monto) if precio else None
    
    s_data = SuscripcionResponse.model_validate(suscripcion)
    s_data.precio_actual = precio
    s_data.costo_mensual_equivalente = costo_mensual
    s_data.historial_precios = historial_ordenado
    return s_data

def actualizar_suscripcion(db: Session, usuario_id: UUID, suscripcion_id: UUID, data: SuscripcionUpdate) -> Suscripcion:
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="No encontramos esa suscripción.")

    update_data = data.model_dump(exclude_unset=True)

    # 1. Validar nombre
    if 'nombre' in update_data:
        if not update_data['nombre'] or not update_data['nombre'].strip():
            raise HTTPException(status_code=400, detail="El nombre de la suscripción no puede estar vacío.")
        suscripcion.nombre = update_data['nombre'].strip()

    # 2. Validar medios de pago
    nueva_billetera_id = update_data['billetera_id'] if 'billetera_id' in update_data else suscripcion.billetera_id
    nueva_tarjeta_id = update_data['tarjeta_id'] if 'tarjeta_id' in update_data else suscripcion.tarjeta_id

    if nueva_billetera_id and nueva_tarjeta_id:
        raise HTTPException(status_code=400, detail="Una suscripción no puede estar vinculada a una billetera y a una tarjeta al mismo tiempo.")

    billetera_obj = None
    if nueva_billetera_id:
        billetera_obj = db.query(Billetera).filter(Billetera.id == nueva_billetera_id, Billetera.usuario_id == usuario_id).first()
        if not billetera_obj:
            raise HTTPException(status_code=404, detail="Billetera no encontrada")

    tarjeta_obj = None
    if nueva_tarjeta_id:
        tarjeta_obj = db.query(TarjetaCredito).filter(TarjetaCredito.id == nueva_tarjeta_id, TarjetaCredito.usuario_id == usuario_id).first()
        if not tarjeta_obj:
            raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    if 'billetera_id' in update_data:
        suscripcion.billetera_id = nueva_billetera_id
    if 'tarjeta_id' in update_data:
        suscripcion.tarjeta_id = nueva_tarjeta_id

    # 3. Validar categoría y subcategoría
    nueva_cat_id = update_data['categoria_id'] if 'categoria_id' in update_data else suscripcion.categoria_id
    nueva_sub_id = update_data['subcategoria_id'] if 'subcategoria_id' in update_data else suscripcion.subcategoria_id

    if nueva_sub_id:
        from app.models.subcategoria import Subcategoria
        sub = db.query(Subcategoria).filter(
            Subcategoria.id == nueva_sub_id
        ).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Subcategoría no encontrada.")
        if nueva_cat_id and sub.categoria_id != nueva_cat_id:
            raise HTTPException(status_code=400, detail="La subcategoría seleccionada no pertenece a la categoría.")

    if 'categoria_id' in update_data:
        suscripcion.categoria_id = nueva_cat_id
    if 'subcategoria_id' in update_data:
        suscripcion.subcategoria_id = nueva_sub_id

    # 4. Validar frecuencia
    if 'frecuencia' in update_data and update_data['frecuencia'] is not None:
        try:
            suscripcion.frecuencia = FrecuenciaSuscripcion(update_data['frecuencia'])
        except ValueError:
            raise HTTPException(status_code=400, detail="Frecuencia de suscripción no válida.")

    # 5. Validar fecha próximo cobro
    if 'proximo_cobro' in update_data and update_data['proximo_cobro'] is not None:
        suscripcion.proximo_cobro = update_data['proximo_cobro']

    # 6. Validar estado
    if 'estado' in update_data and update_data['estado'] is not None:
        try:
            nuevo_estado = EstadoSuscripcion(update_data['estado'])
            if suscripcion.estado == EstadoSuscripcion.CANCELADA and nuevo_estado == EstadoSuscripcion.ACTIVA:
                raise HTTPException(status_code=400, detail="No se puede reactivar una suscripción cancelada. Creá una nueva.")
            suscripcion.estado = nuevo_estado
        except ValueError:
            raise HTTPException(status_code=400, detail="Estado de suscripción no válido.")

    # 7. Actualización de precio si viene monto en el update
    if 'monto' in update_data and update_data['monto'] is not None:
        nuevo_monto = update_data['monto']
        if nuevo_monto <= 0:
            raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero.")
        
        precio_actual = obtener_precio_vigente(db, suscripcion.id)
        nueva_moneda = update_data.get('moneda') or (precio_actual.moneda if precio_actual else 'ARS')

        vigencia = update_data.get('vigente_desde') or hoy_argentina()
        if not precio_actual or precio_actual.monto != nuevo_monto or str(precio_actual.moneda) != str(nueva_moneda) or vigencia != precio_actual.vigente_desde:
            nuevo_precio = HistorialSuscripcion(
                suscripcion_id=suscripcion.id,
                monto=nuevo_monto,
                moneda=nueva_moneda,
                vigente_desde=vigencia
            )
            db.add(nuevo_precio)

    db.commit()
    db.refresh(suscripcion)
    return suscripcion

def actualizar_precio(db: Session, usuario_id: UUID, suscripcion_id: UUID, data: ActualizarPrecioRequest) -> HistorialSuscripcion:
    suscripcion = db.query(Suscripcion).filter(Suscripcion.id == suscripcion_id, Suscripcion.usuario_id == usuario_id).first()
    if not suscripcion:
        raise HTTPException(status_code=404, detail="No encontramos esa suscripción.")

    if data.monto <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor a cero.")

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
        raise HTTPException(status_code=404, detail="No encontramos esa suscripción.")
    
    if suscripcion.estado == EstadoSuscripcion.CANCELADA and nuevo_estado == EstadoSuscripcion.ACTIVA:
        raise HTTPException(status_code=400, detail="No se puede reactivar una suscripción cancelada. Creá una nueva.")
    
    if suscripcion.estado == EstadoSuscripcion.CANCELADA and nuevo_estado == EstadoSuscripcion.CANCELADA:
        raise HTTPException(status_code=400, detail="Esta suscripción ya está cancelada.")

    if suscripcion.estado == nuevo_estado:
        return suscripcion

    if nuevo_estado == EstadoSuscripcion.ACTIVA and suscripcion.proximo_cobro < hoy_argentina():
        suscripcion.proximo_cobro = hoy_argentina()

    suscripcion.estado = nuevo_estado
    db.commit()
    db.refresh(suscripcion)
    return suscripcion

def eliminar_suscripcion(db: Session, usuario_id: UUID, suscripcion_id: UUID) -> None:
    """
    Elimina una suscripción de forma atómica.
    Si está activa o pausada, la cancela primero en la misma transacción.
    No requiere que esté previamente cancelada.
    """
    suscripcion = db.query(Suscripcion).filter(
        Suscripcion.id == suscripcion_id,
        Suscripcion.usuario_id == usuario_id,
    ).first()

    if not suscripcion:
        raise HTTPException(status_code=404, detail="No encontramos esa suscripción.")

    if suscripcion.estado != EstadoSuscripcion.CANCELADA:
        suscripcion.estado = EstadoSuscripcion.CANCELADA

    db.delete(suscripcion)
    db.commit()

def obtener_total_mensual(db: Session, usuario_id: UUID) -> dict:
    suscripciones_activas = obtener_suscripciones(db, usuario_id, estado='activa')
    total_ars = sum(
        (s.costo_mensual_equivalente for s in suscripciones_activas
         if s.precio_actual and s.precio_actual.moneda == 'ARS' and s.costo_mensual_equivalente),
        Decimal('0.00')
    )
    total_usd = sum(
        (s.costo_mensual_equivalente for s in suscripciones_activas
         if s.precio_actual and s.precio_actual.moneda == 'USD' and s.costo_mensual_equivalente),
        Decimal('0.00')
    )
    return { "total_ars": total_ars, "total_usd": total_usd }
