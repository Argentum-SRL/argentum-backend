import logging
from uuid import UUID
from datetime import date, timedelta
from decimal import Decimal
from typing import List, Dict, Any
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException

from app.models.meta import Meta, EstadoMeta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.billetera import Billetera
from app.models.usuario import Moneda
from app.schemas.meta import MetaCreate, MetaUpdate
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)

def obtener_metas(db: Session, usuario_id: UUID, activas_solo: bool = False, skip: int = 0, limit: int = 50) -> List[Meta]:
    query = select(Meta).where(Meta.usuario_id == usuario_id)
    if activas_solo:
        query = query.where(Meta.estado == EstadoMeta.ACTIVA)
    query = query.order_by(desc(Meta.fecha_creacion))
    query = query.offset(skip).limit(limit)
    return db.execute(query).scalars().all()

def obtener_meta(db: Session, usuario_id: UUID, meta_id: UUID) -> Meta:
    query = (
        select(Meta)
        .options(joinedload(Meta.movimientos).joinedload(MovimientoMeta.billetera))
        .where(Meta.id == meta_id, Meta.usuario_id == usuario_id)
    )
    meta = db.execute(query).unique().scalar_one_or_none()
    
    if not meta:
        raise HTTPException(status_code=404, detail="No encontramos esa meta.")
    return meta

def crear_meta(db: Session, usuario_id: UUID, data: MetaCreate) -> Meta:
    if data.fecha_limite and data.fecha_limite < hoy_argentina():
        raise HTTPException(status_code=400, detail="La fecha límite no puede ser en el pasado")
        
    nueva_meta = Meta(
        usuario_id=usuario_id,
        nombre=data.nombre,
        monto_objetivo=data.monto_objetivo,
        moneda=data.moneda,
        monto_actual=data.monto_actual,
        fecha_limite=data.fecha_limite,
        color=data.color,
        nota=data.nota,
        estado=data.estado
    )
    db.add(nueva_meta)
    db.commit()
    db.refresh(nueva_meta)
    return nueva_meta

def actualizar_meta(db: Session, usuario_id: UUID, meta_id: UUID, data: MetaUpdate) -> Meta:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    update_data = data.model_dump(exclude_unset=True)
    
    # Restricción: No cambiar moneda si hay movimientos
    if "moneda" in update_data and update_data["moneda"] != meta.moneda:
        if meta.movimientos:
            raise HTTPException(
                status_code=400, 
                detail="No se puede cambiar la moneda de una meta que ya tiene movimientos registrados"
            )
            
    if "fecha_limite" in update_data and update_data["fecha_limite"] and update_data["fecha_limite"] < hoy_argentina():
         raise HTTPException(status_code=400, detail="La fecha límite no puede ser en el pasado")

    # No permitir pasar a COMPLETADA manualmente si no tiene los fondos
    if "estado" in update_data and update_data["estado"] == EstadoMeta.COMPLETADA:
        if meta.monto_actual < meta.monto_objetivo:
             raise HTTPException(
                status_code=400, 
                detail="No se puede marcar como completada una meta que no ha alcanzado su objetivo"
            )

    for key, value in update_data.items():
        setattr(meta, key, value)
        
    db.commit()
    db.refresh(meta)
    return meta

def eliminar_meta(db: Session, usuario_id: UUID, meta_id: UUID) -> None:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    # Si tiene plata ahorrada, obligamos a retirar antes de borrar
    if meta.monto_actual > 0:
        raise HTTPException(
            status_code=400, 
            detail="No se puede eliminar una meta que aún tiene fondos. Por favor, retirá el dinero primero."
        )

    # Borrado real: Eliminamos movimientos y luego la meta
    if meta.movimientos:
        for mov in meta.movimientos:
            db.delete(mov)
    
    db.delete(meta)
    db.commit()

def registrar_movimiento(db: Session, usuario_id: UUID, meta_id: UUID, data: MovimientoMetaCreate) -> MovimientoMeta:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    # Validar billetera
    billetera = db.get(Billetera, data.billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="Billetera no encontrada")

    from app.services.transaccion_service import _validar_moneda_coincide
    _validar_moneda_coincide(data.moneda_movimiento, billetera)
    
    # El monto que impacta la META debe ser en la moneda de la META.
    monto_impacto_meta = data.monto
    if data.moneda_movimiento != meta.moneda:
        if not data.cotizacion_usada:
             raise HTTPException(status_code=400, detail="Se requiere cotización para movimientos en moneda distinta a la meta")
        
        # Meta en USD, Movimiento en ARS. monto_impacto = monto_ars / cotizacion
        if meta.moneda == Moneda.USD and data.moneda_movimiento == Moneda.ARS:
            monto_impacto_meta = data.monto / data.cotizacion_usada
        # Meta en ARS, Movimiento en USD. monto_impacto = monto_usd * cotizacion
        elif meta.moneda == Moneda.ARS and data.moneda_movimiento == Moneda.USD:
            monto_impacto_meta = data.monto * data.cotizacion_usada

    nuevo_movimiento = MovimientoMeta(
        meta_id=meta_id,
        tipo=data.tipo,
        monto=data.monto,
        moneda_movimiento=data.moneda_movimiento,
        cotizacion_usada=data.cotizacion_usada,
        tipo_dolar_usado=data.tipo_dolar_usado,
        billetera_id=data.billetera_id,
        fecha=data.fecha
    )
    
    if data.tipo == TipoMovimientoMeta.APORTE:
        # Validar saldo suficiente en billetera
        if billetera.saldo_actual < data.monto:
            raise HTTPException(
                status_code=400, 
                detail=f"Saldo insuficiente en la billetera '{billetera.nombre}'"
            )
            
        # Crear la transacción vinculada (EGRESO)
        from app.schemas.transaccion import TransaccionCreate
        from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion
        from app.services.transaccion_service import crear_transaccion
        cat_id = obtener_o_crear_categoria_ahorro(db, usuario_id, "egreso")
        tx_create = TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=data.monto,
            moneda=data.moneda_movimiento,
            fecha=data.fecha,
            descripcion=f"Aporte a la meta: {meta.nombre}",
            categoria_id=cat_id,
            subcategoria_id=None,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            billetera_id=data.billetera_id,
            tarjeta_id=None,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=None
        )
        crear_transaccion(db, usuario_id, tx_create, commit=False)
        meta.monto_actual += monto_impacto_meta
    else:
        # Validar que no se retire más de lo que hay
        if meta.monto_actual < monto_impacto_meta:
             raise HTTPException(status_code=400, detail="Monto insuficiente en la meta")
        
        # Crear la transacción vinculada (INGRESO)
        from app.schemas.transaccion import TransaccionCreate
        from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion
        from app.services.transaccion_service import crear_transaccion

        cat_id = obtener_o_crear_categoria_ahorro(db, usuario_id, "ingreso")
        tx_create = TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=data.monto,
            moneda=data.moneda_movimiento,
            fecha=data.fecha,
            descripcion=f"Retiro de la meta: {meta.nombre}",
            categoria_id=cat_id,
            subcategoria_id=None,
            metodo_pago=MetodoPago.TRANSFERENCIA,
            billetera_id=data.billetera_id,
            tarjeta_id=None,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=None
        )
        crear_transaccion(db, usuario_id, tx_create, commit=False)
        meta.monto_actual -= monto_impacto_meta
    
    # Actualizar estado si se completó (Lógica automática)
    if meta.monto_actual >= meta.monto_objetivo:
        estado_anterior = meta.estado  # capturar ANTES de cambiar
        meta.estado = EstadoMeta.COMPLETADA
        if estado_anterior != EstadoMeta.COMPLETADA:  # solo si recién se completó
            try:
                from app.services.notificacion_service import obtener_configuracion, resolver_canales_notificacion, crear_notificacion
                from app.models.notificacion import TipoNotificacion, NivelNotificacion
                config = obtener_configuracion(db, usuario_id)
                canales = resolver_canales_notificacion(config, TipoNotificacion.META_ALCANZADA)
                if canales is not None:
                    canal_web, canal_whatsapp = canales
                    crear_notificacion(
                        db=db,
                        usuario_id=usuario_id,
                        tipo=TipoNotificacion.META_ALCANZADA,
                        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                        mensaje=f"¡Completaste tu meta '{meta.nombre}'! Ahorraste ${meta.monto_objetivo:,.0f}.",
                        entidad_tipo="meta",
                        entidad_id=meta.id,
                        deep_link="/app/metas",
                        canal_web=canal_web,
                        canal_whatsapp=canal_whatsapp,
                    )
            except Exception:
                pass
    elif meta.estado == EstadoMeta.COMPLETADA and meta.monto_actual < meta.monto_objetivo:
        meta.estado = EstadoMeta.ACTIVA

    db.add(nuevo_movimiento)
    db.commit()
    db.refresh(nuevo_movimiento)
    return nuevo_movimiento

def eliminar_movimiento(db: Session, usuario_id: UUID, meta_id: UUID, movimiento_id: UUID) -> None:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    movimiento = db.get(MovimientoMeta, movimiento_id)
    if not movimiento or movimiento.meta_id != meta_id:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")
        
    # Revertir impacto
    billetera = db.get(Billetera, movimiento.billetera_id)
    
    monto_impacto_meta = movimiento.monto
    if movimiento.moneda_movimiento != meta.moneda:
        if meta.moneda == Moneda.USD and movimiento.moneda_movimiento == Moneda.ARS:
            monto_impacto_meta = movimiento.monto / movimiento.cotizacion_usada
        elif meta.moneda == Moneda.ARS and movimiento.moneda_movimiento == Moneda.USD:
            monto_impacto_meta = movimiento.monto * movimiento.cotizacion_usada

    # Revertir impacto en saldo / transacciones
    from app.models.transaccion import Transaccion
    from app.services.transaccion_service import eliminar_transaccion
    
    desc_buscada = f"Aporte a la meta: {meta.nombre}" if movimiento.tipo == TipoMovimientoMeta.APORTE else f"Retiro de la meta: {meta.nombre}"
    tx = db.query(Transaccion).filter(
        Transaccion.usuario_id == usuario_id,
        Transaccion.billetera_id == movimiento.billetera_id,
        Transaccion.monto == movimiento.monto,
        Transaccion.fecha == movimiento.fecha,
        Transaccion.descripcion == desc_buscada
    ).first()

    if tx:
        eliminar_transaccion(db, usuario_id, tx.id)
    else:
        # Fallback sin transaccion vinculada
        if movimiento.tipo == TipoMovimientoMeta.APORTE:
            if billetera:
                try:
                    from app.services.transaccion_service import _validar_moneda_coincide
                    _validar_moneda_coincide(movimiento.moneda_movimiento, billetera)
                    billetera.saldo_actual += movimiento.monto
                except Exception as e:
                    logger.critical(f"Error crítico de inconsistencia de moneda al revertir aporte de meta {movimiento.id}: {e}")
        else:
            if billetera:
                try:
                    from app.services.transaccion_service import _validar_moneda_coincide
                    _validar_moneda_coincide(movimiento.moneda_movimiento, billetera)
                    billetera.saldo_actual -= movimiento.monto
                except Exception as e:
                    logger.critical(f"Error crítico de inconsistencia de moneda al revertir retiro de meta {movimiento.id}: {e}")

    # Revertir en la meta
    if movimiento.tipo == TipoMovimientoMeta.APORTE:
        meta.monto_actual -= monto_impacto_meta
    else:
        meta.monto_actual += monto_impacto_meta

    # Actualizar estado
    if meta.monto_actual >= meta.monto_objetivo:
        meta.estado = EstadoMeta.COMPLETADA
    else:
        meta.estado = EstadoMeta.ACTIVA

    db.delete(movimiento)
    db.commit()

def obtener_analytics(db: Session, usuario_id: UUID, meta_id: UUID) -> Dict[str, Any]:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    # Historial de aportes mensuales (últimos 12 meses)
    hoy = hoy_argentina()
    un_anio_atras = hoy - timedelta(days=365)
    
    movimientos = db.execute(
        select(MovimientoMeta)
        .where(
            MovimientoMeta.meta_id == meta_id,
            MovimientoMeta.fecha >= un_anio_atras
        )
        .order_by(MovimientoMeta.fecha)
    ).scalars().all()
    
    # Agrupar por mes
    historial_mensual = {}
    for m in movimientos:
        mes_key = m.fecha.strftime("%Y-%m")
        impacto = m.monto
        if m.moneda_movimiento != meta.moneda:
             # Usar la cotización guardada en el movimiento
             if meta.moneda == Moneda.USD and m.moneda_movimiento == Moneda.ARS:
                 impacto = m.monto / m.cotizacion_usada
             elif meta.moneda == Moneda.ARS and m.moneda_movimiento == Moneda.USD:
                 impacto = m.monto * m.cotizacion_usada
        
        if m.tipo == TipoMovimientoMeta.RETIRO:
            impacto = -impacto
            
        historial_mensual[mes_key] = historial_mensual.get(mes_key, Decimal("0")) + impacto

    # Convertir a lista para el frontend
    chart_data = [{"mes": k, "monto": float(v)} for k, v in sorted(historial_mensual.items())]

    # Savings velocity (promedio de los últimos 3 meses)
    tres_meses_atras = hoy - timedelta(days=90)
    aportes_recientes = [m for m in movimientos if m.tipo == TipoMovimientoMeta.APORTE and m.fecha >= tres_meses_atras]
    
    suma_aportes = Decimal("0")
    for m in aportes_recientes:
        impacto = m.monto
        if m.moneda_movimiento != meta.moneda:
             if meta.moneda == Moneda.USD and m.moneda_movimiento == Moneda.ARS:
                 impacto = m.monto / m.cotizacion_usada
             elif meta.moneda == Moneda.ARS and m.moneda_movimiento == Moneda.USD:
                 impacto = m.monto * m.cotizacion_usada
        suma_aportes += impacto
        
    velocidad_mensual = suma_aportes / 3
    
    # Proyección
    faltante = meta.monto_objetivo - meta.monto_actual
    meses_restantes = None
    fecha_estimada = None
    
    if velocidad_mensual > 0 and faltante > 0:
        meses_restantes = float(faltante / velocidad_mensual)
        fecha_estimada = hoy + timedelta(days=int(meses_restantes * 30))

    return {
        "chart_data": chart_data,
        "velocidad_mensual": float(velocidad_mensual),
        "meses_restantes": meses_restantes,
        "fecha_estimada_finalizacion": fecha_estimada,
        "porcentaje_progreso": float((meta.monto_actual / meta.monto_objetivo) * 100) if meta.monto_objetivo > 0 else 0,
        "monto_faltante": float(faltante) if faltante > 0 else 0
    }

def obtener_summary(db: Session, usuario_id: UUID) -> Dict[str, Any]:
    metas = obtener_metas(db, usuario_id, activas_solo=True)
    
    total_objetivo_ars = Decimal("0")
    total_actual_ars = Decimal("0")
    # Para el summary, simplificamos a ARS usando una cotización base o simplemente contando
    
    count_completadas = db.execute(
        select(func.count(Meta.id)).where(Meta.usuario_id == usuario_id, Meta.estado == EstadoMeta.COMPLETADA)
    ).scalar() or 0
    
    return {
        "total_metas": len(metas),
        "completadas": count_completadas,
        "proximo_vencimiento": min([m.fecha_limite for m in metas if m.fecha_limite and m.fecha_limite >= hoy_argentina()], default=None)
    }

# ==============================================================================
# ADVERTENCIA PREVENTIVA:
# Esta función busca la categoría contable "Ahorro" por NOMBRE (nombre == "Ahorro").
# Si no existe, la crea dinámicamente en tiempo de ejecución.
# 
# Esta categoría es la que utiliza el flujo de Metas para registrar las transacciones
# espejo de egreso (aportes) e ingreso (retiros) que impactan el saldo de las billeteras.
# NO debe eliminarse ni renombrarse en código sin una migración previa que actualice
# también las transacciones históricas existentes vinculadas a su ID.
# ==============================================================================

def obtener_o_crear_categoria_ahorro(db: Session, usuario_id: UUID, tipo: str) -> UUID:
    from app.models.categoria import Categoria, TipoCategoria, EstadoCategoria
    
    tipo_cat = TipoCategoria.EGRESO if tipo == "egreso" else TipoCategoria.INGRESO
    
    # Buscar categoría "Ahorro"
    categoria = db.query(Categoria).filter(
        (Categoria.creador_id == usuario_id) | (Categoria.es_global == True),
        Categoria.nombre == "Ahorro",
        Categoria.tipo == tipo_cat
    ).first()

    if not categoria:
        categoria = Categoria(
            nombre="Ahorro",
            tipo=tipo_cat,
            icono="ahorro",
            color="#2563EB",
            es_global=True,
            creador_id=None,
            estado=EstadoCategoria.ACTIVA
        )
        db.add(categoria)
        db.flush()
        
    return categoria.id
