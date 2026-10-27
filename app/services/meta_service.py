import logging
from uuid import UUID
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException

from app.models.meta import Meta, EstadoMeta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.billetera import Billetera
from app.models.transaccion import TipoTransaccion
from app.models.usuario import Moneda
from app.schemas.meta import MetaCreate, MetaUpdate
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.services.saldo_billetera_service import aplicar_impacto_saldo, bloquear_billeteras
from app.services import presupuesto_service
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)

TOLERANCIA_COTIZACION_PORCENTAJE = Decimal("0.30")

def obtener_metas(db: Session, usuario_id: UUID, activas_solo: bool = False, skip: int = 0, limit: int = 50) -> List[Meta]:
    query = (
        select(Meta)
        .options(joinedload(Meta.movimientos).joinedload(MovimientoMeta.billetera))
        .where(Meta.usuario_id == usuario_id)
    )
    if activas_solo:
        query = query.where(Meta.estado == EstadoMeta.ACTIVA)
    query = query.order_by(desc(Meta.fecha_creacion))
    query = query.offset(skip).limit(limit)
    return list(db.execute(query).unique().scalars().all())

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

def recalcular_acumulado(db: Session, meta: Meta) -> Decimal:
    # Único punto que escribe meta.monto_actual a partir de la suma de sus movimientos (Decisión 5).
    db.flush()
    movimientos = (
        db.execute(
            select(MovimientoMeta).where(MovimientoMeta.meta_id == meta.id)
        )
        .scalars()
        .all()
    )
    meta_moneda = meta.moneda.value if hasattr(meta.moneda, "value") else str(meta.moneda)
    acumulado = Decimal("0.00")
    for mov in sorted(movimientos, key=lambda m: (m.fecha, str(m.id))):
        mov_moneda = mov.moneda_movimiento.value if hasattr(mov.moneda_movimiento, "value") else str(mov.moneda_movimiento)
        monto_mov = Decimal(str(mov.monto))
        if mov_moneda == meta_moneda:
            impacto = monto_mov.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            if mov.cotizacion_usada is None or Decimal(str(mov.cotizacion_usada)) <= Decimal("0"):
                raise ValueError("Movimiento de meta entre monedas sin cotización válida.")
            cot = Decimal(str(mov.cotizacion_usada))
            if meta_moneda == "USD":
                impacto = (monto_mov / cot).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                impacto = (monto_mov * cot).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tipo_str = mov.tipo.value if hasattr(mov.tipo, "value") else str(mov.tipo)
        if tipo_str == TipoMovimientoMeta.APORTE.value:
            acumulado += impacto
        else:
            acumulado -= impacto

    acumulado = acumulado.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if acumulado < Decimal("0.00"):
        raise HTTPException(
            status_code=400,
            detail="No se puede borrar este aporte porque la meta quedaría en negativo. Borrá primero el retiro.",
        )

    meta.monto_actual = acumulado
    estado_actual = meta.estado.value if hasattr(meta.estado, "value") else str(meta.estado)
    if estado_actual != EstadoMeta.PAUSADA.value:
        if acumulado >= Decimal(str(meta.monto_objetivo)):
            meta.estado = EstadoMeta.COMPLETADA
        elif estado_actual == EstadoMeta.COMPLETADA.value and acumulado < Decimal(str(meta.monto_objetivo)):
            meta.estado = EstadoMeta.ACTIVA

    db.flush()
    return acumulado


def crear_meta(db: Session, usuario_id: UUID, data: MetaCreate) -> Meta:
    nombre_limpio = data.nombre.strip()
    if not nombre_limpio:
        raise HTTPException(status_code=400, detail="El nombre de la meta no puede estar vacío.")

    if data.monto_objetivo <= Decimal("0"):
        raise HTTPException(status_code=400, detail="El monto objetivo tiene que ser mayor a cero.")

    if data.monto_actual is not None and Decimal(str(data.monto_actual)) > Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail="Una meta nueva arranca en cero: cargá un aporte para sumarle plata.",
        )

    if data.fecha_limite and data.fecha_limite < hoy_argentina():
        raise HTTPException(status_code=400, detail="La fecha límite no puede ser en el pasado.")
        
    nueva_meta = Meta(
        usuario_id=usuario_id,
        nombre=nombre_limpio,
        monto_objetivo=data.monto_objetivo,
        moneda=data.moneda,
        monto_actual=Decimal("0.00"),
        fecha_limite=data.fecha_limite,
        color=data.color,
        nota=data.nota.strip() if data.nota else None,
        estado=data.estado
    )
    db.add(nueva_meta)
    db.commit()
    db.refresh(nueva_meta)
    return nueva_meta

def actualizar_meta(db: Session, usuario_id: UUID, meta_id: UUID, data: MetaUpdate) -> Meta:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    update_data = data.model_dump(exclude_unset=True)

    if "nombre" in update_data:
        nombre_limpio = update_data["nombre"].strip() if update_data["nombre"] else ""
        if not nombre_limpio:
            raise HTTPException(status_code=400, detail="El nombre de la meta no puede estar vacío.")
        update_data["nombre"] = nombre_limpio

    if "monto_objetivo" in update_data:
        if update_data["monto_objetivo"] <= Decimal("0"):
            raise HTTPException(status_code=400, detail="El monto objetivo tiene que ser mayor a cero.")
    
    # Restricción: No cambiar moneda si hay movimientos
    if "moneda" in update_data and update_data["moneda"] != meta.moneda:
        if meta.movimientos:
            raise HTTPException(
                status_code=400, 
                detail="No se puede cambiar la moneda de una meta que ya tiene movimientos registrados."
            )
            
    if "fecha_limite" in update_data and update_data["fecha_limite"] and update_data["fecha_limite"] < hoy_argentina():
        raise HTTPException(status_code=400, detail="La fecha límite no puede ser en el pasado.")

    objetivo_final = update_data.get("monto_objetivo", meta.monto_objetivo)

    # No permitir pasar a COMPLETADA manualmente si no tiene los fondos
    if "estado" in update_data and update_data["estado"] == EstadoMeta.COMPLETADA:
        if meta.monto_actual < objetivo_final:
            raise HTTPException(
                status_code=400, 
                detail="No se puede marcar como completada una meta que no ha alcanzado su objetivo."
            )

    if "nota" in update_data and update_data["nota"]:
        update_data["nota"] = update_data["nota"].strip()

    for key, value in update_data.items():
        setattr(meta, key, value)

    # Ajuste automático si el nuevo objetivo se cumplió
    if meta.monto_actual >= meta.monto_objetivo and meta.estado == EstadoMeta.ACTIVA:
        meta.estado = EstadoMeta.COMPLETADA
    elif meta.monto_actual < meta.monto_objetivo and meta.estado == EstadoMeta.COMPLETADA:
        meta.estado = EstadoMeta.ACTIVA
        
    db.commit()
    db.refresh(meta)
    return obtener_meta(db, usuario_id, meta_id)

def eliminar_meta(db: Session, usuario_id: UUID, meta_id: UUID) -> None:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    # Si tiene plata ahorrada, obligamos a retirar antes de borrar
    if meta.monto_actual > Decimal("0"):
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

def registrar_movimiento(
    db: Session,
    usuario_id: UUID,
    meta_id: UUID,
    data: MovimientoMetaCreate,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> MovimientoMeta:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    if data.monto <= Decimal("0"):
        raise HTTPException(status_code=400, detail="El monto del movimiento tiene que ser mayor a cero.")

    # Validar billetera
    billetera = db.get(Billetera, data.billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="Billetera no encontrada.")

    from app.models.billetera import EstadoBilletera
    if billetera.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(status_code=400, detail="La billetera seleccionada no está activa.")

    from app.services.transaccion_service import _validar_moneda_coincide
    _validar_moneda_coincide(data.moneda_movimiento, billetera)
    
    # El monto que impacta la META debe ser en la moneda de la META.
    monto_impacto_meta = data.monto
    if data.moneda_movimiento != meta.moneda:
        if not data.cotizacion_usada or data.cotizacion_usada <= Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail="Tenés que indicar una cotización válida mayor a cero para operar entre monedas distintas."
            )

        if data.cotizacion_usada == Decimal("1"):
            raise HTTPException(
                status_code=400,
                detail="La cotización no puede ser 1 cuando la meta y el movimiento son en monedas distintas."
            )

        from app.services.dolar_service import get_cotizaciones_dolar, obtener_cotizacion_por_fecha
        from app.models.cotizacion_dolar import CotizacionDolar
        cotizacion_referencia: Decimal | None = None
        tipo = (data.tipo_dolar_usado or "blue").lower()
        try:
            cots_data = get_cotizaciones_dolar()
            cots = cots_data.get("cotizaciones", {})
            info_dolar = cots.get(tipo) or cots.get("blue") or cots.get("oficial")
            if info_dolar and info_dolar.get("promedio"):
                cotizacion_referencia = Decimal(str(info_dolar["promedio"]))
            elif info_dolar and info_dolar.get("venta"):
                cotizacion_referencia = Decimal(str(info_dolar["venta"]))
        except Exception as e:
            logger.warning("No se pudo consultar cotización de referencia en API para meta: %s", e)
            cotizacion_referencia = None

        # Si no hubo respuesta de la API externa, recurrir a la última cotización en la base local
        if cotizacion_referencia is None:
            cot_db = obtener_cotizacion_por_fecha(db, tipo, hoy_argentina())
            if cot_db is not None:
                cotizacion_referencia = cot_db.promedio or cot_db.venta or cot_db.compra
            else:
                stmt_any = select(CotizacionDolar).order_by(desc(CotizacionDolar.fecha)).limit(1)
                cot_any = db.execute(stmt_any).scalars().first()
                if cot_any is not None:
                    cotizacion_referencia = cot_any.promedio or cot_any.venta or cot_any.compra

        if cotizacion_referencia is not None:
            min_permitido = (cotizacion_referencia * (Decimal("1") - TOLERANCIA_COTIZACION_PORCENTAJE)).quantize(Decimal("0.01"))
            max_permitido = (cotizacion_referencia * (Decimal("1") + TOLERANCIA_COTIZACION_PORCENTAJE)).quantize(Decimal("0.01"))
            if data.cotizacion_usada < min_permitido or data.cotizacion_usada > max_permitido:
                raise HTTPException(
                    status_code=400,
                    detail=f"La cotización ingresada ({formatear_monto(data.cotizacion_usada, Moneda.ARS)}) difiere más del 30% de la cotización actual ({formatear_monto(cotizacion_referencia, Moneda.ARS)}). Ingresá un valor entre {formatear_monto(min_permitido, Moneda.ARS)} y {formatear_monto(max_permitido, Moneda.ARS)}."
                )

        # Meta en USD, Movimiento en ARS. monto_impacto = monto_ars / cotizacion
        if meta.moneda == Moneda.USD and data.moneda_movimiento == Moneda.ARS:
            monto_impacto_meta = (data.monto / data.cotizacion_usada).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        # Meta en ARS, Movimiento en USD. monto_impacto = monto_usd * cotizacion
        elif meta.moneda == Moneda.ARS and data.moneda_movimiento == Moneda.USD:
            monto_impacto_meta = (data.monto * data.cotizacion_usada).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
    db.add(nuevo_movimiento)
    db.flush()
    
    if data.tipo == TipoMovimientoMeta.APORTE:
        # Bloquear billetera antes de validar saldo suficiente
        bloquear_billeteras(db, [billetera.id], usuario_id)

        # Validar saldo suficiente en billetera
        if billetera.saldo_actual < data.monto:
            raise HTTPException(
                status_code=400, 
                detail=f"Saldo insuficiente en la billetera '{billetera.nombre}'."
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
            estado_verificacion=None,
            movimiento_meta_id=nuevo_movimiento.id
        )
        tx = crear_transaccion(db, usuario_id, tx_create, commit=False)
        tx.movimiento_meta_id = nuevo_movimiento.id
    else:
        # Validar que no se retire más de lo que hay
        if meta.monto_actual < monto_impacto_meta:
            raise HTTPException(status_code=400, detail="Monto insuficiente en la meta.")
        
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
            estado_verificacion=None,
            movimiento_meta_id=nuevo_movimiento.id
        )
        tx = crear_transaccion(db, usuario_id, tx_create, commit=False)
        tx.movimiento_meta_id = nuevo_movimiento.id

    estado_anterior = meta.estado
    db.flush()
    recalcular_acumulado(db, meta)

    if meta.estado == EstadoMeta.COMPLETADA and estado_anterior != EstadoMeta.COMPLETADA:
        try:
            from app.services.notificacion_service import obtener_configuracion, resolver_canales_notificacion, crear_notificacion
            from app.models.notificacion import TipoNotificacion, NivelNotificacion
            config = obtener_configuracion(db, usuario_id, commit=False)
            canales = resolver_canales_notificacion(config, TipoNotificacion.META_ALCANZADA)
            if canales is not None:
                canal_web, canal_whatsapp = canales
                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.META_ALCANZADA,
                    nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                    mensaje=f"¡Completaste tu meta '{meta.nombre}'! Ahorraste {formatear_monto(meta.monto_objetivo, meta.moneda)}.",
                    entidad_tipo="meta",
                    entidad_id=meta.id,
                    deep_link="/app/metas",
                    canal_web=canal_web,
                    canal_whatsapp=canal_whatsapp,
                    commit=False,
                    datos_template={"nombre": meta.nombre, "monto_fmt": formatear_monto(meta.monto_objetivo, meta.moneda)},
                )
        except Exception:
            logger.warning("No se pudo crear la notificación de meta alcanzada", exc_info=True)

    if commit:
        db.commit()
        db.refresh(nuevo_movimiento)
    else:
        db.flush()
    return nuevo_movimiento

def eliminar_movimiento(
    db: Session,
    usuario_id: UUID,
    meta_id: UUID,
    movimiento_id: UUID,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> None:
    meta = obtener_meta(db, usuario_id, meta_id)
    
    movimiento = db.get(MovimientoMeta, movimiento_id)
    if not movimiento or movimiento.meta_id != meta_id:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado.")
        
    # Revertir impacto
    billetera = db.get(Billetera, movimiento.billetera_id)

    # Revertir impacto en saldo / transacciones
    from app.models.transaccion import Transaccion
    
    # 1. Búsqueda directa y precisa por identificador de movimiento de meta
    tx = db.query(Transaccion).filter(
        Transaccion.usuario_id == usuario_id,
        Transaccion.movimiento_meta_id == movimiento.id
    ).first()

    # 2. Fallback por compatibilidad con movimientos históricos no vinculados
    if not tx:
        desc_exacta = f"Aporte a la meta: {meta.nombre}" if movimiento.tipo == TipoMovimientoMeta.APORTE else f"Retiro de la meta: {meta.nombre}"
        desc_patron = f"%Aporte a la meta:%" if movimiento.tipo == TipoMovimientoMeta.APORTE else f"%Retiro de la meta:%"
        
        tx = db.query(Transaccion).filter(
            Transaccion.usuario_id == usuario_id,
            Transaccion.billetera_id == movimiento.billetera_id,
            Transaccion.monto == movimiento.monto,
            Transaccion.fecha == movimiento.fecha,
            Transaccion.descripcion == desc_exacta
        ).first()

        if not tx:
            tx = db.query(Transaccion).filter(
                Transaccion.usuario_id == usuario_id,
                Transaccion.billetera_id == movimiento.billetera_id,
                Transaccion.monto == movimiento.monto,
                Transaccion.fecha == movimiento.fecha,
                Transaccion.descripcion.like(desc_patron)
            ).first()

    mov_moneda = movimiento.moneda_movimiento
    db.delete(movimiento)
    db.flush()
    try:
        recalcular_acumulado(db, meta)
    except HTTPException:
        if commit:
            db.rollback()
        raise

    # Revertir impacto en saldo / transacciones
    if tx:
        from app.services.transaccion_service import _afecta_saldo, _validar_moneda_coincide
        if _afecta_saldo(tx) and tx.saldo_aplicado and billetera:
            try:
                _validar_moneda_coincide(mov_moneda, billetera)
                impacto_tx = tx.monto if tx.tipo == TipoTransaccion.INGRESO else -tx.monto
                aplicar_impacto_saldo(billetera, -impacto_tx, tx.fecha)
            except Exception as e:
                logger.critical(f"Error crítico al revertir saldo de meta {movimiento_id}: {e}")
        presupuesto_service.registrar_impacto_presupuesto(db, tx, revertir=True, commit=False)
        db.delete(tx)
    else:
        logger.warning(f"Movimiento de meta {movimiento_id} no tiene transacción vinculada: no se toca el saldo.")
    if commit:
        db.commit()
    else:
        db.flush()

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
        Categoria.nombre == "Ahorro",
        Categoria.tipo == tipo_cat
    ).first()

    if not categoria:
        categoria = Categoria(
            nombre="Ahorro",
            tipo=tipo_cat,
            icono="ahorro",
            color="#2563EB",
            estado=EstadoCategoria.ACTIVA
        )
        db.add(categoria)
        db.flush()
        from app.services.categoria_service import invalidar_cache_categorias_globales
        invalidar_cache_categorias_globales()
    elif categoria.estado != EstadoCategoria.ACTIVA:
        categoria.estado = EstadoCategoria.ACTIVA
        db.flush()
        from app.services.categoria_service import invalidar_cache_categorias_globales
        invalidar_cache_categorias_globales()
        
    return categoria.id
