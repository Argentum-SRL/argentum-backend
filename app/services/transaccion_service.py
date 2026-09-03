import logging
from uuid import UUID
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select, desc, or_, delete, not_, and_
from sqlalchemy.orm import Session, joinedload
from dateutil.relativedelta import relativedelta

from app.models.transaccion import Transaccion, TipoTransaccion, OrigenTransaccion, EstadoVerificacionTransaccion, MetodoPago
from app.models.billetera import Billetera
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.tarjeta_credito import TarjetaCredito
from app.models.saldo_arrastrado import SaldoArrastradoTarjeta, PagoSaldoArrastrado, EstadoSaldoArrastrado
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate
from app.services.tarjeta_service import calcular_primer_vencimiento, _tabla_saldo_arrastrado_existe
from app.services import cuotas_service, presupuesto_service
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto

logger = logging.getLogger(__name__)


def obtener_transacciones(
    db: Session, 
    usuario_id: UUID, 
    skip: int = 0, 
    limit: int = 500,
    billetera_id: Optional[UUID] = None,
    tipo: Optional[TipoTransaccion] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    categoria_id: Optional[UUID] = None,
    categoria_ids: Optional[list] = None,
    subcategoria_id: Optional[UUID] = None,
    moneda: Optional[str] = None,
    estado_verificacion: Optional[str] = None,
    busqueda: Optional[str] = None,
    es_cuota_hija: Optional[bool] = None
):
    # El usuario solo ve transacciones normales e hijas. Nunca las "padre de cuotas".
    # Las transacciones pagadas con crédito (compras en 1 pago o cuotas) se excluyen de la lista general
    # para evitar duplicaciones; solo se cuenta el pago consolidado del resumen (que se registra como debito).
    query = select(Transaccion).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.es_padre_cuotas == False,
        not_(and_(Transaccion.es_cuota_hija == True, Transaccion.metodo_pago == MetodoPago.CREDITO))
    )
    
    if billetera_id:
        query = query.where(Transaccion.billetera_id == billetera_id)
    if tipo:
        query = query.where(Transaccion.tipo == tipo)
    if fecha_desde:
        query = query.where(Transaccion.fecha >= fecha_desde)
    if fecha_hasta:
        query = query.where(Transaccion.fecha <= fecha_hasta)
    if categoria_ids and len(categoria_ids) > 0:
        query = query.where(Transaccion.categoria_id.in_(categoria_ids))
    elif categoria_id:
        query = query.where(Transaccion.categoria_id == categoria_id)
    if subcategoria_id:
        query = query.where(Transaccion.subcategoria_id == subcategoria_id)
    if moneda:
        query = query.where(Transaccion.moneda == moneda)
    if estado_verificacion:
        query = query.where(Transaccion.estado_verificacion == estado_verificacion)
    if busqueda and busqueda.strip():
        from app.models.categoria import Categoria
        from app.models.subcategoria import Subcategoria
        from app.models.billetera import Billetera
        
        term = busqueda.strip()
        query = (
            query
            .outerjoin(Transaccion.categoria)
            .outerjoin(Transaccion.subcategoria)
            .outerjoin(Transaccion.billetera)
            .where(
                or_(
                    Transaccion.descripcion.ilike(f"%{term}%"),
                    Categoria.nombre.ilike(f"%{term}%"),
                    Subcategoria.nombre.ilike(f"%{term}%"),
                    Billetera.nombre.ilike(f"%{term}%"),
                    Transaccion.metodo_pago.ilike(f"%{term}%")
                )
            )
        )
    if es_cuota_hija is not None:
        query = query.where(Transaccion.es_cuota_hija == es_cuota_hija)
        
    query = query.order_by(desc(Transaccion.fecha), desc(Transaccion.fecha_creacion))
    
    query = query.options(joinedload(Transaccion.subcategoria))
    
    transacciones = db.execute(query.offset(skip).limit(limit)).scalars().all()
    
    return transacciones


def obtener_transaccion(db: Session, usuario_id: UUID, transaccion_id: UUID) -> Transaccion:
    transaccion = db.execute(
        select(Transaccion).where(
            Transaccion.id == transaccion_id, 
            Transaccion.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not transaccion:
        raise HTTPException(status_code=404, detail="No encontramos esa transacción.")
    return transaccion


def _hoy_argentina() -> date:
    """Retorna la fecha actual en hora Argentina (America/Argentina/Buenos_Aires)."""
    return hoy_argentina()


def _afecta_saldo(transaccion) -> bool:
    """True si la transacción debe/debió impactar el saldo de la billetera."""
    return (
        transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE
        and transaccion.fecha <= _hoy_argentina()
        and transaccion.metodo_pago != MetodoPago.CREDITO
    )


def _validar_moneda_coincide(moneda_operacion, billetera: Billetera) -> None:
    """Valida que la moneda de la transacción coincida con la de la billetera."""
    op_val = moneda_operacion.value if hasattr(moneda_operacion, "value") else str(moneda_operacion)
    bill_val = billetera.moneda.value if hasattr(billetera.moneda, "value") else str(billetera.moneda)
    if op_val != bill_val:
        raise HTTPException(
            status_code=400,
            detail=f"La moneda de la operación ({op_val}) no coincide con la moneda de la billetera ({bill_val})."
        )


def deducir_metodo_pago(
    billetera: Optional[Billetera], 
    tarjeta_id: Optional[UUID] = None
) -> MetodoPago:
    """
    Deduce el método de pago de una transacción según las siguientes reglas en orden de prioridad:
    1. Si la transacción tiene tarjeta_id asignado (no nulo) -> MetodoPago.CREDITO.
    2. Si no, y la billetera tiene es_efectivo == True -> MetodoPago.EFECTIVO.
    3. En cualquier otro caso -> MetodoPago.DEBITO.

    Si billetera es None (y tarjeta_id es None), se emite un log de nivel WARNING
    y se devuelve MetodoPago.DEBITO. Nunca retorna None.
    """
    if tarjeta_id is not None:
        return MetodoPago.CREDITO
    if billetera is None:
        logger.warning("Billetera no provista al deducir método de pago; usando DEBITO por defecto.")
        return MetodoPago.DEBITO
    if getattr(billetera, "es_efectivo", False):
        return MetodoPago.EFECTIVO
    return MetodoPago.DEBITO


def _validar_tarjeta(db: Session, tarjeta_id: UUID, usuario_id: UUID) -> TarjetaCredito:
    """Valida que la tarjeta pertenezca al usuario."""
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == tarjeta_id,
        TarjetaCredito.usuario_id == usuario_id
    ).first()
    if not tarjeta:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    return tarjeta


def _evaluar_gasto_inusual_safe(usuario_id: UUID, transaccion_id: UUID) -> None:
    """Wrapper seguro para evaluar_gasto_inusual en background tasks. Abre su propia sesión de DB."""
    from app.core.database import SessionLocal
    db = SessionLocal()
    try:
        transaccion = db.get(Transaccion, transaccion_id)
        if transaccion:
            evaluar_gasto_inusual(db, usuario_id, transaccion)
    except Exception as e:
        logger.warning(f"Error en background evaluar_gasto_inusual para tx {transaccion_id}: {e}")
    finally:
        db.close()


def crear_transaccion(db: Session, usuario_id: UUID, data: TransaccionCreate, commit: bool = True, background_tasks: Optional[BackgroundTasks] = None) -> Transaccion:
    # 1. Validar billetera
    billetera = db.execute(
        select(Billetera).where(
            Billetera.id == data.billetera_id,
            Billetera.usuario_id == usuario_id
        )
    ).scalar_one_or_none()

    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    # Tarea 1.4: La validación de moneda contra la billetera sigue vigente para todo lo que
    # NO sea un consumo con tarjeta de crédito (en crédito la plata no sale de la billetera en el momento).
    es_consumo_credito = bool(data.tarjeta_id is not None and (data.metodo_pago == MetodoPago.CREDITO or data.metodo_pago is None))
    if not es_consumo_credito:
        _validar_moneda_coincide(data.moneda, billetera)

    # 2. Validar categoría obligatoria
    if not data.categoria_id:
        raise HTTPException(status_code=400, detail="Debés seleccionar una categoría.")

    from app.models.categoria import Categoria, EstadoCategoria
    categoria = db.execute(
        select(Categoria).where(
            Categoria.id == data.categoria_id,
            Categoria.estado == EstadoCategoria.ACTIVA
        )
    ).scalar_one_or_none()

    if not categoria:
        raise HTTPException(status_code=404, detail="No encontramos esa categoría.")

    # 3. Validar subcategoría si se proporcionó
    if data.subcategoria_id:
        from app.models.subcategoria import Subcategoria
        subcategoria = db.execute(
            select(Subcategoria).where(
                Subcategoria.id == data.subcategoria_id,
                Subcategoria.categoria_id == data.categoria_id
            )
        ).scalar_one_or_none()
        if not subcategoria:
            raise HTTPException(status_code=400, detail="La subcategoría no pertenece a la categoría seleccionada.")
    
    # 4. Manejo de Cuotas
    # DECISIÓN DE PRODUCTO: Todo consumo con tarjeta de crédito genera siempre un grupo de cuotas,
    # aunque sea de un solo pago. Se unifica el modelo de datos.
    if data.metodo_pago == MetodoPago.CREDITO and data.tarjeta_id and not data.es_cuota_hija and not data.es_padre_cuotas:
        from app.schemas.transaccion import InfoCuotas
        data.es_padre_cuotas = True
        if not data.info_cuotas:
            data.info_cuotas = InfoCuotas(
                cantidad_cuotas=1,
                cuota_inicial=1,
                tiene_interes=False,
                tasa_interes=None,
                monto_total=data.monto,
                proximo_resumen=False
            )

    if data.es_padre_cuotas:
        if not data.info_cuotas:
            raise HTTPException(status_code=400, detail="Para registrar una compra en cuotas, completá los datos de las cuotas.")
        
        # Crear transaccion padre (no impacta saldo)
        nueva_transaccion = Transaccion(
            **data.model_dump(exclude={"usuario_id", "info_cuotas", "monto"}),
            usuario_id=usuario_id,
            monto=data.info_cuotas.monto_total # Guardamos el total en el padre para registro
        )
        db.add(nueva_transaccion)
        db.flush()

        # Calculo Amortizacion Francesa
        monto_total = data.info_cuotas.monto_total
        cant = data.info_cuotas.cantidad_cuotas
        
        # Asegurar que cant sea al menos 1 para evitar DivisionByZero
        cant = max(1, data.info_cuotas.cantidad_cuotas)
        
        if data.info_cuotas.tiene_interes and data.info_cuotas.tasa_interes:
            tasa_mensual = data.info_cuotas.tasa_interes / 100
            if tasa_mensual > 0:
                monto_cuota = monto_total * (tasa_mensual * (1 + tasa_mensual)**cant) / ((1 + tasa_mensual)**cant - 1)
            else:
                monto_cuota = monto_total / cant
        else:
            monto_cuota = monto_total / cant

        total_financiado = monto_cuota * cant

        if data.metodo_pago == MetodoPago.CREDITO and not data.tarjeta_id:
            raise HTTPException(
                status_code=422,
                detail="Tenés que seleccionar una tarjeta para registrar una compra en cuotas con crédito."
            )

        # Determinar primer vencimiento
        primer_vencimiento = None
        if data.metodo_pago == MetodoPago.CREDITO and data.tarjeta_id:
            tarjeta = _validar_tarjeta(db, data.tarjeta_id, usuario_id)
            proximo_resumen = data.info_cuotas.proximo_resumen if data.info_cuotas else False
            primer_vencimiento = calcular_primer_vencimiento(
                data.fecha, tarjeta.dia_cierre, tarjeta.dia_vencimiento, proximo_resumen
            )
        elif data.primer_vencimiento_manual:
            primer_vencimiento = data.primer_vencimiento_manual
        else:
            # Comportamiento anterior: mes siguiente
            primer_vencimiento = data.fecha + relativedelta(months=1)

        grupo = GrupoCuotas(
            usuario_id=usuario_id,
            transaccion_padre_id=nueva_transaccion.id,
            tarjeta_id=data.tarjeta_id,
            descripcion=data.descripcion,
            monto_total=monto_total,
            cantidad_cuotas=cant,
            tiene_interes=data.info_cuotas.tiene_interes,
            tasa_interes=data.info_cuotas.tasa_interes,
            total_financiado=total_financiado,
            moneda=data.moneda,
            primer_vencimiento=primer_vencimiento
        )
        db.add(grupo)
        db.flush()

        # Generar cuotas usando el nuevo servicio
        cuotas_service.crear_cuotas(
            db=db,
            transaccion_padre=nueva_transaccion,
            grupo=grupo,
            cantidad_cuotas=cant,
            primer_vencimiento=primer_vencimiento,
            monto_cuota=monto_cuota,
            usuario_id=str(usuario_id),
            cuota_inicial=data.info_cuotas.cuota_inicial
        )
        
        # Actualizar la transaccion padre con el link al grupo (opcional pero util)
        nueva_transaccion.grupo_cuotas_id = grupo.id

            # Al crear un grupo de cuotas, NINGUNA impacta el saldo hoy
            # porque la primera empieza el mes que viene.
        
        if commit:
            db.commit()
            db.refresh(nueva_transaccion)
        else:
            db.flush()
        return nueva_transaccion

    # 3. Transacción normal
    if data.tarjeta_id:
        _validar_tarjeta(db, data.tarjeta_id, usuario_id)

    nueva_transaccion = Transaccion(
        **data.model_dump(exclude={"usuario_id", "info_cuotas"}),
        usuario_id=usuario_id
    )
    
    # 4. Actualizar saldo solo si es confirmada, es hoy o pasada, y NO es crédito
    # (El crédito impacta vía el pago del resumen consolidado)
    if _afecta_saldo(nueva_transaccion):
        if nueva_transaccion.tipo == TipoTransaccion.INGRESO:
            billetera.saldo_actual += nueva_transaccion.monto
        else:
            billetera.saldo_actual -= nueva_transaccion.monto
            if billetera.saldo_actual <= 0:
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
                            mensaje=f"Tu billetera '{billetera.nombre}' quedó sin saldo disponible.",
                            entidad_tipo="billetera",
                            entidad_id=billetera.id,
                            deep_link="/app/billeteras",
                            canal_web=canal_web,
                            canal_whatsapp=canal_whatsapp,
                        )
                except Exception:
                    pass

            # Solo si tiene categoría asignada y hay suficiente historial
            if nueva_transaccion.categoria_id is not None:
                if background_tasks is not None:
                    # Ejecutar en background para no bloquear el request (evita llamadas HTTP externas lentas)
                    background_tasks.add_task(_evaluar_gasto_inusual_safe, usuario_id, nueva_transaccion.id)
                else:
                    try:
                        evaluar_gasto_inusual(db, usuario_id, nueva_transaccion)
                    except Exception:
                        pass
        
    # Impacto en presupuestos
    presupuesto_service.registrar_impacto_presupuesto(db, nueva_transaccion, revertir=False)

    db.add(nueva_transaccion)
    if commit:
        db.commit()
        db.refresh(nueva_transaccion)
    else:
        db.flush()
    
    return nueva_transaccion


def actualizar_transaccion(db: Session, usuario_id: UUID, transaccion_id: UUID, data: TransaccionUpdate) -> Transaccion:
    transaccion = obtener_transaccion(db, usuario_id, transaccion_id)
    
    # Validar que la nueva moneda (o la actual) coincida con la nueva billetera (o la actual)
    billetera_id = data.billetera_id if data.billetera_id is not None else transaccion.billetera_id
    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")
    
    _validar_moneda_coincide(data.moneda if data.moneda is not None else transaccion.moneda, billetera)

    # Validar tarjeta si se actualiza tarjeta_id
    if data.tarjeta_id is not None:
        _validar_tarjeta(db, data.tarjeta_id, usuario_id)

    # Impacto en presupuestos (Revertir con datos viejos)
    presupuesto_service.registrar_impacto_presupuesto(db, transaccion, revertir=True)

    CAMPOS_FINANCIEROS_CUOTA = {
        'monto', 'moneda', 'tipo', 'billetera_id',
        'tarjeta_id', 'metodo_pago', 'fecha', 'estado_verificacion'
    }

    CAMPOS_PERMITIDOS_CUOTA = {'descripcion', 'categoria_id', 'subcategoria_id'}

    # Si es una transacción de cuotas, verificar que solo se editen campos permitidos
    if transaccion.es_cuota_hija or transaccion.es_padre_cuotas:
        datos_update = data.model_dump(exclude_unset=True)
        campos_financieros_modificados = []

        for campo in CAMPOS_FINANCIEROS_CUOTA:
            if campo in datos_update:
                valor_actual = getattr(transaccion, campo, None)
                valor_nuevo = datos_update[campo]
                # Comparar con conversión de tipos para evitar falsos positivos
                if str(valor_actual) != str(valor_nuevo) and valor_actual != valor_nuevo:
                    campos_financieros_modificados.append(campo)

        if campos_financieros_modificados:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No podés modificar los campos financieros de una transacción en cuotas "
                    f"({', '.join(campos_financieros_modificados)}). "
                    f"Solo podés editar la descripción y la categoría."
                )
            )

    impacto_saldo_cambia = any([
        data.monto is not None,
        data.tipo is not None,
        data.billetera_id is not None and data.billetera_id != transaccion.billetera_id,
        data.fecha is not None,
        data.estado_verificacion is not None,
        data.metodo_pago is not None and data.metodo_pago != transaccion.metodo_pago
    ])
    
    if impacto_saldo_cambia:
        # Revertir impacto anterior si existia
        if _afecta_saldo(transaccion):
            billetera_vieja = db.get(Billetera, transaccion.billetera_id)
            if billetera_vieja:
                try:
                    _validar_moneda_coincide(transaccion.moneda, billetera_vieja)
                    if transaccion.tipo == TipoTransaccion.INGRESO:
                        billetera_vieja.saldo_actual -= transaccion.monto
                    else:
                        billetera_vieja.saldo_actual += transaccion.monto
                except Exception as e:
                    logger.error(f"Inconsistencia al revertir saldo de billetera vieja {billetera_vieja.id} para tx {transaccion.id}: {e}")

        update_data = data.model_dump(exclude_unset=True)
        if "descripcion" in update_data and update_data["descripcion"] is None:
            update_data["descripcion"] = ""
        for key, value in update_data.items():
            setattr(transaccion, key, value)

        # Aplicar nuevo impacto
        if _afecta_saldo(transaccion):
            billetera_nueva = db.get(Billetera, transaccion.billetera_id)
            if not billetera_nueva or billetera_nueva.usuario_id != usuario_id:
                raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

            _validar_moneda_coincide(transaccion.moneda, billetera_nueva)
            if transaccion.tipo == TipoTransaccion.INGRESO:
                billetera_nueva.saldo_actual += transaccion.monto
            else:
                billetera_nueva.saldo_actual -= transaccion.monto
                if billetera_nueva.saldo_actual <= 0:
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
                                mensaje=f"Tu billetera '{billetera_nueva.nombre}' quedó sin saldo disponible.",
                                entidad_tipo="billetera",
                                entidad_id=billetera_nueva.id,
                                deep_link="/app/billeteras",
                                canal_web=canal_web,
                                canal_whatsapp=canal_whatsapp,
                            )
                    except Exception:
                        pass
    else:
        update_data = data.model_dump(exclude_unset=True)
        if "descripcion" in update_data and update_data["descripcion"] is None:
            update_data["descripcion"] = ""
        for key, value in update_data.items():
            setattr(transaccion, key, value)
            
    # Impacto en presupuestos (Aplicar con datos nuevos)
    presupuesto_service.registrar_impacto_presupuesto(db, transaccion, revertir=False)

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
            # 2. Revertir saldo (solo si no es crédito, aunque las cuotas suelen serlo)
            cuotas = db.execute(
                select(Cuota).options(joinedload(Cuota.transaccion)).where(Cuota.grupo_id == grupo.id)
            ).scalars().all()
            for c in cuotas:
                if c.pagada or c.fecha_vencimiento <= _hoy_argentina():
                    tx_hija = c.transaccion
                    if tx_hija and tx_hija.metodo_pago != MetodoPago.CREDITO:
                        b = db.get(Billetera, tx_hija.billetera_id)
                        if b:
                            try:
                                _validar_moneda_coincide(tx_hija.moneda, b)
                                if tx_hija.tipo == TipoTransaccion.INGRESO:
                                    b.saldo_actual -= tx_hija.monto
                                else:
                                    b.saldo_actual += tx_hija.monto
                            except Exception as e:
                                logger.critical(f"Error crítico de inconsistencia de moneda al eliminar cuota hija {tx_hija.id}: {e}")
            
            # 3. Romper dependencias circulares antes de borrar
            id_hijas = [c.transaccion_id for c in cuotas]
            id_padre = grupo.transaccion_padre_id
            
            # Nullify references in all transactions involved
            db.execute(
                delete(Cuota).where(Cuota.grupo_id == grupo.id)
            )
            
            # Update involved transactions to remove FK to the group
            from sqlalchemy import update
            where_clause = Transaccion.id == id_padre
            if id_hijas:
                where_clause = or_(where_clause, Transaccion.id.in_(id_hijas))
                
            db.execute(
                update(Transaccion)
                .where(where_clause)
                .values(grupo_cuotas_id=None)
            )
            db.flush()

            # 4. Eliminar en orden
            db.execute(delete(GrupoCuotas).where(GrupoCuotas.id == grupo.id))
            if id_hijas:
                db.execute(delete(Transaccion).where(Transaccion.id.in_(id_hijas)))
            db.execute(delete(Transaccion).where(Transaccion.id == id_padre))
            
            db.commit()
            return {"detail": "Grupo de cuotas eliminado exitosamente"}

    # Revertir impacto en metas si es una transacción vinculada a una meta
    if transaccion.descripcion.startswith("Aporte a la meta:") or transaccion.descripcion.startswith("Retiro de la meta:"):
        from app.models.meta import Meta, EstadoMeta
        from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
        from app.models.usuario import Moneda
        
        prefix_aporte = "Aporte a la meta: "
        prefix_retiro = "Retiro de la meta: "
        
        if transaccion.descripcion.startswith(prefix_aporte):
            meta_nombre = transaccion.descripcion[len(prefix_aporte):]
            tipo_mov = TipoMovimientoMeta.APORTE
        else:
            meta_nombre = transaccion.descripcion[len(prefix_retiro):]
            tipo_mov = TipoMovimientoMeta.RETIRO
            
        meta = db.query(Meta).filter(Meta.usuario_id == usuario_id, Meta.nombre == meta_nombre).first()
        if meta:
            movimiento = db.query(MovimientoMeta).filter(
                MovimientoMeta.meta_id == meta.id,
                MovimientoMeta.billetera_id == transaccion.billetera_id,
                MovimientoMeta.monto == transaccion.monto,
                MovimientoMeta.fecha == transaccion.fecha,
                MovimientoMeta.tipo == tipo_mov
            ).first()
            
            if movimiento:
                monto_impacto_meta = movimiento.monto
                if movimiento.moneda_movimiento != meta.moneda:
                    if meta.moneda == Moneda.USD and movimiento.moneda_movimiento == Moneda.ARS:
                        monto_impacto_meta = movimiento.monto / movimiento.cotizacion_usada
                    elif meta.moneda == Moneda.ARS and movimiento.moneda_movimiento == Moneda.USD:
                        monto_impacto_meta = movimiento.monto * movimiento.cotizacion_usada
 
                if tipo_mov == TipoMovimientoMeta.APORTE:
                    meta.monto_actual -= monto_impacto_meta
                else:
                    meta.monto_actual += monto_impacto_meta
                    
                # Ajustar estado de la meta
                if meta.monto_actual >= meta.monto_objetivo:
                    meta.estado = EstadoMeta.COMPLETADA
                else:
                    meta.estado = EstadoMeta.ACTIVA
                    
                db.delete(movimiento)

    # Reversión de pago de resumen de tarjeta (Etapa 3A y 3B):
    if _tabla_saldo_arrastrado_existe(db):
        # 1. Si esta transacción redujo un saldo arrastrado, se devuelve el saldo a su monto anterior
        #    y si estaba saldado, vuelve a estado activo (Tarea 5.4)
        reducciones_saldo = (
            db.query(PagoSaldoArrastrado)
            .filter(PagoSaldoArrastrado.transaccion_pago_id == transaccion.id)
            .all()
        )
        for red in reducciones_saldo:
            saldo = db.get(SaldoArrastradoTarjeta, red.saldo_arrastrado_id)
            if saldo:
                saldo.monto_restante += red.monto_aplicado
                if saldo.monto_restante > Decimal("0") and saldo.estado == EstadoSaldoArrastrado.SALDADO:
                    saldo.estado = EstadoSaldoArrastrado.ACTIVO
            db.delete(red)

        # 2. Si se elimina el pago que generó un saldo arrastrado, ese saldo desaparece (Tarea 5.3)
        saldos_originados = (
            db.query(SaldoArrastradoTarjeta)
            .filter(SaldoArrastradoTarjeta.transaccion_origen_id == transaccion.id)
            .all()
        )
        for s_orig in saldos_originados:
            db.delete(s_orig)

    # 3. Revertir ÚNICAMENTE las cuotas saldadas por esta transacción (Etapa 3A)
    cuotas_revertir = (
        db.query(Cuota)
        .filter(Cuota.transaccion_pago_id == transaccion.id)
        .all()
    )
    for c in cuotas_revertir:
        c.pagada = False
        c.transaccion_pago_id = None

    # 4. Reversión de percepción impositiva vinculada a este pago (Etapa 3C - Tarea 4.2 y 4.3)
    percepciones_vinculadas = (
        db.query(Transaccion)
        .filter(Transaccion.pago_origen_id == transaccion.id)
        .all()
    )
    for p in percepciones_vinculadas:
        if _afecta_saldo(p):
            b_p = db.get(Billetera, p.billetera_id)
            if b_p:
                b_p.saldo_actual += p.monto
        presupuesto_service.registrar_impacto_presupuesto(db, p, revertir=True)
        db.delete(p)

    # Transaccion normal
    if _afecta_saldo(transaccion):
        billetera = db.get(Billetera, transaccion.billetera_id)
        if billetera:
            try:
                _validar_moneda_coincide(transaccion.moneda, billetera)
                if transaccion.tipo == TipoTransaccion.INGRESO:
                    billetera.saldo_actual -= transaccion.monto
                else:
                    billetera.saldo_actual += transaccion.monto
            except Exception as e:
                logger.critical(f"Error crítico de inconsistencia de moneda al eliminar transacción {transaccion.id}: {e}")
            
    # Impacto en presupuestos
    presupuesto_service.registrar_impacto_presupuesto(db, transaccion, revertir=True)

    db.delete(transaccion)
    db.commit()

    return {"detail": "Transacción eliminada exitosamente"}


def confirmar_transaccion_ia(db: Session, usuario_id: UUID, transaccion_id: UUID) -> Transaccion:
    transaccion = obtener_transaccion(db, usuario_id, transaccion_id)
    
    if transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE:
        raise HTTPException(status_code=400, detail="La transacción ya está confirmada o no requiere verificación.")
        
    transaccion.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
    
    # Al confirmar, impacta el saldo si es fecha presente/pasada o si es un pago de resumen confirmado por el usuario
    hoy = hoy_argentina()
    debe_impactar_saldo = (transaccion.fecha <= hoy or transaccion.pago_resumen_vencimiento is not None)
    if debe_impactar_saldo and transaccion.metodo_pago != MetodoPago.CREDITO:
        billetera = db.get(Billetera, transaccion.billetera_id)
        if not billetera:
            raise HTTPException(status_code=404, detail="No encontramos esa billetera.")
            
        _validar_moneda_coincide(transaccion.moneda, billetera)

        if transaccion.tipo == TipoTransaccion.INGRESO:
            billetera.saldo_actual += transaccion.monto
        else:
            billetera.saldo_actual -= transaccion.monto
            if billetera.saldo_actual <= 0:
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
                            mensaje=f"Tu billetera '{billetera.nombre}' quedó sin saldo disponible.",
                            entidad_tipo="billetera",
                            entidad_id=billetera.id,
                            deep_link="/app/billeteras",
                            canal_web=canal_web,
                            canal_whatsapp=canal_whatsapp,
                        )
                except Exception:
                    pass

            # Solo si tiene categoría asignada y hay suficiente historial
            if transaccion.categoria_id is not None:
                try:
                    evaluar_gasto_inusual(db, usuario_id, transaccion)
                except Exception:
                    pass

    # Si esta transacción es un pago de resumen de tarjeta (creada por job o manual):
    # 1. Marcar como pagadas las cuotas de dicho resumen (incluye atrasadas que arrastre el resumen) y vincularlas
    if transaccion.pago_resumen_vencimiento and transaccion.tarjeta_id:
        cuotas_a_pagar = (
            db.query(Cuota)
            .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
            .filter(
                GrupoCuotas.tarjeta_id == transaccion.tarjeta_id,
                Cuota.pagada == False,
                Cuota.fecha_vencimiento <= transaccion.pago_resumen_vencimiento
            )
            .all()
        )
        for c in cuotas_a_pagar:
            c.pagada = True
            c.transaccion_pago_id = transaccion.id

        # 2. Cancelar cualquier saldo arrastrado activo para esta tarjeta hasta este vencimiento (Tarea 6.2)
        if _tabla_saldo_arrastrado_existe(db):
            saldos_activos = (
                db.query(SaldoArrastradoTarjeta)
                .filter(
                    SaldoArrastradoTarjeta.tarjeta_id == transaccion.tarjeta_id,
                    SaldoArrastradoTarjeta.estado == EstadoSaldoArrastrado.ACTIVO,
                    SaldoArrastradoTarjeta.fecha_vencimiento_resumen <= transaccion.pago_resumen_vencimiento
                )
                .all()
            )
            for s in saldos_activos:
                if s.monto_restante > Decimal("0"):
                    monto_ap = s.monto_restante
                    s.monto_restante = Decimal("0")
                    s.estado = EstadoSaldoArrastrado.SALDADO
                    db.add(PagoSaldoArrastrado(
                        saldo_arrastrado_id=s.id,
                        transaccion_pago_id=transaccion.id,
                        monto_aplicado=monto_ap
                    ))
            
    # Impacto en presupuestos
    presupuesto_service.registrar_impacto_presupuesto(db, transaccion, revertir=False)

    db.commit()
    db.refresh(transaccion)

    # Trigger: recalcular perfil financiero en background
    try:
        from app.services.perfil_financiero_service import recalcular_perfil_tras_confirmacion
        recalcular_perfil_tras_confirmacion(db, usuario_id)
    except Exception:
        pass  # No interrumpir el flujo principal si falla

    return transaccion



def obtener_pendientes_ia(db: Session, usuario_id: UUID, skip: int = 0, limit: int = 100):
    return db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE,
            Transaccion.origen.in_([
                OrigenTransaccion.IA_WPP,
                OrigenTransaccion.IA_CHAT,
                OrigenTransaccion.IA_PDF,
            ]),
            Transaccion.es_padre_cuotas == False,
        )
        .options(joinedload(Transaccion.subcategoria))
        .order_by(desc(Transaccion.fecha), desc(Transaccion.fecha_creacion))
        .offset(skip)
        .limit(limit)
    ).scalars().all()


def evaluar_gasto_inusual(db: Session, usuario_id: UUID, transaccion: Transaccion) -> None:
    """
    Evalúa si una transacción de egreso es inusual y genera una notificación.
    Utiliza tres niveles de sensibilidad según el volumen de historial de la categoría.
    """
    from app.models.usuario import Moneda
    if transaccion.categoria_id is None or transaccion.tipo != TipoTransaccion.EGRESO:
        return

    # 1. Obtener historial de transacciones de egreso en la misma categoría y moneda
    # Excluyendo transacciones pendientes y la transacción actual evaluada
    stmt = (
        select(Transaccion)
        .where(
            and_(
                Transaccion.usuario_id == usuario_id,
                Transaccion.categoria_id == transaccion.categoria_id,
                Transaccion.tipo == TipoTransaccion.EGRESO,
                Transaccion.moneda == transaccion.moneda,
                Transaccion.es_padre_cuotas == False,
                or_(
                    Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
                    Transaccion.estado_verificacion.is_(None)
                ),
                Transaccion.id != transaccion.id
            )
        )
    )
    historial = db.execute(stmt).scalars().all()
    count = len(historial)

    if count < 12:
        return

    # 2. Ajustar montos por inflación si la moneda es ARS (USD nunca se ajusta)
    montos_historicos = []
    if transaccion.moneda == Moneda.ARS:
        from app.services.tools_service import ajustar_por_ipc
        for tx in historial:
            adjusted = ajustar_por_ipc(monto=float(tx.monto), fecha_origen=tx.fecha.strftime("%Y-%m-%d"), db=db)
            montos_historicos.append(float(adjusted))
    else:
        montos_historicos = [float(tx.monto) for tx in historial]

    monto_actual = float(transaccion.monto)

    from app.services.notificacion_service import crear_notificacion
    from app.models.notificacion import TipoNotificacion, NivelNotificacion
    from app.models.categoria import Categoria

    categoria = db.get(Categoria, transaccion.categoria_id)
    categoria_nombre = categoria.nombre if categoria else "esta categoría"
    simbolo = "US$ " if transaccion.moneda == Moneda.USD else "$"

    if count < 30:
        # NIVEL 1: Conservador (12 a 29 transacciones)
        # Basado en Mediana y MAD
        def calcular_mediana(valores: list[float]) -> float:
            n = len(valores)
            if n == 0:
                return 0.0
            sorted_val = sorted(valores)
            mid = n // 2
            if n % 2 == 1:
                return sorted_val[mid]
            else:
                return (sorted_val[mid - 1] + sorted_val[mid]) / 2.0

        mediana = calcular_mediana(montos_historicos)
        desviaciones = [abs(val - mediana) for val in montos_historicos]
        mad = calcular_mediana(desviaciones)

        dispara = False
        if mad == 0:
            threshold = mediana * 1.5
            dispara = monto_actual > threshold
        else:
            z_modificado = 0.6745 * (monto_actual - mediana) / mad
            dispara = z_modificado > 3.5

        if dispara:
            monto_fmt = formatear_monto(monto_actual, transaccion.moneda)
            mediana_fmt = formatear_monto(mediana, transaccion.moneda)
            mensaje = f"Registramos un gasto inusual: gastaste {monto_fmt} en {categoria_nombre}, pero tu gasto habitual en esa categoría es de {mediana_fmt}."
            from app.services.notificacion_service import obtener_configuracion, resolver_canales_notificacion
            config = obtener_configuracion(db, usuario_id)
            canales = resolver_canales_notificacion(config, TipoNotificacion.GASTO_INUSUAL)
            if canales is not None:
                canal_web, canal_whatsapp = canales
                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.GASTO_INUSUAL,
                    nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
                    mensaje=mensaje,
                    entidad_tipo="transaccion",
                    entidad_id=transaccion.id,
                    deep_link="/app/transacciones",
                    canal_web=canal_web,
                    canal_whatsapp=canal_whatsapp,
                )
    else:
        # NIVEL 2 y 3: count >= 30
        # Basado en promedio ajustado y perfil financiero (tasa de ahorro + saldo disponible)
        promedio_ajustado = sum(montos_historicos) / len(montos_historicos)

        from app.models.perfil_financiero import PerfilFinanciero
        perfil = db.execute(
            select(PerfilFinanciero).where(PerfilFinanciero.usuario_id == usuario_id)
        ).scalar_one_or_none()

        from app.services.contexto_financiero_service import _calcular_saldo_disponible_sync
        from app.models.usuario import Usuario

        # Si el perfil financiero no tiene datos suficientes, usar 2.0 y nivel informativa por defecto
        multiplicador = 2.0
        nivel = NivelNotificacion.FINANCIERA_INFORMATIVA

        # 1. Tasa de ahorro
        tasa_ahorro = None
        if perfil:
            if transaccion.moneda == Moneda.ARS:
                tasa_ahorro = perfil.tasa_ahorro_ars
            elif transaccion.moneda == Moneda.USD:
                tasa_ahorro = perfil.tasa_ahorro_usd

        # 2. Saldo disponible post-gasto
        disponible_res = _calcular_saldo_disponible_sync(db, usuario_id)
        moneda_str = "ars" if transaccion.moneda == Moneda.ARS else "usd"
        saldo_info = disponible_res.get(moneda_str)
        saldo_disponible = saldo_info.get("saldo_disponible") if saldo_info else None

        # 3. Ingreso promedio mensual
        usuario = db.get(Usuario, usuario_id)
        hoy_dt = hoy_argentina()
        if usuario:
            from app.services.dashboard_service import get_ciclo_fechas
            inicio_ciclo, _ = get_ciclo_fechas(usuario, hoy_dt)
            inicio_analisis = min(
                inicio_ciclo - timedelta(days=60),
                hoy_dt - timedelta(days=90)
            )
        else:
            inicio_analisis = hoy_dt - timedelta(days=90)

        from sqlalchemy import func
        stmt_ingresos = (
            select(func.sum(Transaccion.monto))
            .where(
                and_(
                    Transaccion.usuario_id == usuario_id,
                    Transaccion.tipo == TipoTransaccion.INGRESO,
                    Transaccion.moneda == transaccion.moneda,
                    or_(
                        Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
                        Transaccion.estado_verificacion.is_(None)
                    ),
                    Transaccion.fecha >= inicio_analisis,
                    Transaccion.fecha <= hoy_dt
                )
            )
        )
        total_ingresos = db.execute(stmt_ingresos).scalar() or Decimal("0")
        cant_meses = Decimal(str(max(1.0, (hoy_dt - inicio_analisis).days / 30.0)))
        ingreso_promedio_mensual = total_ingresos / cant_meses

        # Modulación del multiplicador y nivel (excluyente, prioridad a saldo bajo/negativo)
        if (
            saldo_disponible is None
            or saldo_disponible < Decimal("0.10") * ingreso_promedio_mensual
            or saldo_disponible < Decimal("0")
        ):
            multiplicador = 2.0 - 0.75
            nivel = NivelNotificacion.FINANCIERA_IMPORTANTE
        elif (
            tasa_ahorro is not None
            and tasa_ahorro > Decimal("0.30")
            and saldo_disponible is not None
            and saldo_disponible > Decimal("0")
        ):
            multiplicador = 2.0 + 0.75
            nivel = NivelNotificacion.FINANCIERA_INFORMATIVA
        else:
            multiplicador = 2.0
            nivel = NivelNotificacion.FINANCIERA_INFORMATIVA

        multiplicador = max(multiplicador, 1.2)

        if monto_actual > promedio_ajustado * multiplicador:
            mensaje = f"Registramos un gasto inusual: gastaste {simbolo}{monto_actual:,.0f} en {categoria_nombre}, pero tu gasto habitual en esa categoría es de {simbolo}{promedio_ajustado:,.0f}."
            from app.services.notificacion_service import obtener_configuracion, resolver_canales_notificacion
            config = obtener_configuracion(db, usuario_id)
            canales = resolver_canales_notificacion(config, TipoNotificacion.GASTO_INUSUAL)
            if canales is not None:
                canal_web, canal_whatsapp = canales
                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.GASTO_INUSUAL,
                    nivel=nivel,
                    mensaje=mensaje,
                    entidad_tipo="transaccion",
                    entidad_id=transaccion.id,
                    deep_link="/app/transacciones",
                    canal_web=canal_web,
                    canal_whatsapp=canal_whatsapp,
                )

