import logging
from uuid import UUID
from datetime import date, datetime, timedelta
from decimal import Decimal
import calendar
from typing import Optional, List
from sqlalchemy import select, func, or_, desc
from sqlalchemy.orm import Session, joinedload, selectinload
from fastapi import HTTPException

from app.models.presupuesto import Presupuesto, PeriodoPresupuestoTipo, RenovacionPresupuesto, EstadoPresupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.notificacion import TipoNotificacion, NivelNotificacion
from app.models.usuario import Usuario, Moneda
from app.schemas.presupuesto import PresupuestoCreate, PresupuestoUpdate
from app.services.whatsapp_service import enviar_mensaje_whatsapp

logger = logging.getLogger(__name__)

def _validar_categorias_presupuesto(db: Session, categorias_input: List) -> None:
    if not categorias_input:
        raise HTTPException(status_code=400, detail="Debe seleccionar al menos una categoría")

    cat_ids = {c.categoria_id for c in categorias_input if c.categoria_id}
    subcat_ids = {c.subcategoria_id for c in categorias_input if getattr(c, 'subcategoria_id', None)}

    if cat_ids:
        cats = db.execute(
            select(Categoria).where(Categoria.id.in_(cat_ids))
        ).scalars().all()
        found_cat_ids = {c.id for c in cats}
        if len(found_cat_ids) != len(cat_ids):
            raise HTTPException(status_code=404, detail="Categoría no encontrada")
        for cat in cats:
            if cat.tipo != TipoCategoria.EGRESO:
                raise HTTPException(
                    status_code=400, 
                    detail="Los presupuestos solo pueden asociarse a categorías de egreso"
                )

    if subcat_ids:
        subs = db.execute(
            select(Subcategoria).options(joinedload(Subcategoria.categoria)).where(Subcategoria.id.in_(subcat_ids))
        ).scalars().all()
        found_subcat_ids = {s.id for s in subs}
        if len(found_subcat_ids) != len(subcat_ids):
            raise HTTPException(status_code=404, detail="Categoría no encontrada")
        for sub in subs:
            if sub.categoria and sub.categoria.tipo != TipoCategoria.EGRESO:
                raise HTTPException(
                    status_code=400, 
                    detail="Los presupuestos solo pueden asociarse a categorías de egreso"
                )

def formatear_monto(monto: Decimal) -> str:
    # Formato simple para notificaciones
    return f"${monto:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def calcular_fechas_periodo(periodo: str, fecha_referencia: date):
    year = fecha_referencia.year
    month = fecha_referencia.month
    
    if periodo == PeriodoPresupuestoTipo.MENSUAL.value:
        fecha_inicio = date(year, month, 1)
        fecha_fin = date(year, month, calendar.monthrange(year, month)[1])
        return fecha_inicio, fecha_fin
        
    if periodo == PeriodoPresupuestoTipo.QUINCENAL.value:
        if fecha_referencia.day <= 15:
            fecha_inicio = date(year, month, 1)
            fecha_fin = date(year, month, 15)
        else:
            fecha_inicio = date(year, month, 16)
            fecha_fin = date(year, month, calendar.monthrange(year, month)[1])
        return fecha_inicio, fecha_fin
        
    if periodo == PeriodoPresupuestoTipo.SEMANAL.value:
        weekday = fecha_referencia.weekday() # 0=Lunes
        fecha_inicio = fecha_referencia - timedelta(days=weekday)
        fecha_fin = fecha_inicio + timedelta(days=6)
        return fecha_inicio, fecha_fin
    
    raise ValueError(f"Periodo inválido: {periodo}")

def calcular_gasto_en_periodo(
    db: Session, 
    usuario_id: UUID, 
    categorias_input: List, 
    fecha_inicio: date, 
    fecha_fin: date,
    moneda: Optional[Moneda | str] = None
) -> Decimal:
    # categorías_input puede ser PresupuestoCategoriaInput (schema) o PresupuestoCategoria (modelo)
    # Regla: Si tiene subcategoria_id especificado, aplica solo a esa subcategoría.
    # Si tiene categoria_id pero no subcategoria_id, aplica a toda la categoría padre.
    cat_ids = [c.categoria_id for c in categorias_input if c.categoria_id and not getattr(c, 'subcategoria_id', None)]
    subcat_ids = [c.subcategoria_id for c in categorias_input if getattr(c, 'subcategoria_id', None)]
    
    query = select(func.sum(Transaccion.monto)).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        or_(
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.estado_verificacion == None
        ),
        Transaccion.es_padre_cuotas == False,
        Transaccion.fecha >= fecha_inicio,
        Transaccion.fecha <= fecha_fin
    )
    
    if moneda is not None:
        moneda_enum = moneda if isinstance(moneda, Moneda) else Moneda(moneda)
        query = query.where(Transaccion.moneda == moneda_enum)
    
    conditions = []
    if cat_ids:
        conditions.append(or_(
            Transaccion.categoria_id.in_(cat_ids),
            Transaccion.subcategoria_id.in_(
                select(Subcategoria.id).where(Subcategoria.categoria_id.in_(cat_ids))
            )
        ))
    if subcat_ids:
        conditions.append(Transaccion.subcategoria_id.in_(subcat_ids))
        
    if not conditions:
        return Decimal("0")
        
    query = query.where(or_(*conditions))
    
    resultado = db.execute(query).scalar()
    return resultado if resultado else Decimal("0")

def obtener_periodo_activo(db: Optional[Session], presupuesto: Presupuesto) -> Optional[PeriodoPresupuesto]:
    hoy = date.today()
    if not presupuesto.periodos:
        return None
    return next(
        (p for p in presupuesto.periodos if p.fecha_inicio <= hoy <= p.fecha_fin),
        None
    )

def obtener_presupuestos(db: Session, usuario_id: UUID, estado: Optional[str] = None) -> List[Presupuesto]:
    query = (
        select(Presupuesto)
        .options(
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.categoria),
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria),
            selectinload(Presupuesto.periodos)
        )
        .where(Presupuesto.usuario_id == usuario_id)
    )
    
    if estado:
        query = query.where(Presupuesto.estado == estado)
        
    return db.execute(query).scalars().all()

def crear_presupuesto(db: Session, usuario_id: UUID, data: PresupuestoCreate) -> Presupuesto:
    if data.monto <= 0:
        raise HTTPException(status_code=400, detail="Monto límite debe ser positivo")

    _validar_categorias_presupuesto(db, data.categorias)

    try:
        moneda_enum = Moneda(data.moneda)
    except ValueError:
        raise HTTPException(status_code=400, detail="Moneda inválida")
    try:
        periodo_enum = PeriodoPresupuestoTipo(data.periodo)
    except ValueError:
        raise HTTPException(status_code=400, detail="Periodo inválido")
    try:
        renovacion_enum = RenovacionPresupuesto(data.renovacion)
    except ValueError:
        raise HTTPException(status_code=400, detail="Tipo de renovación inválido")

    # 1. Crear registro Presupuesto
    nuevo_presupuesto = Presupuesto(
        usuario_id=usuario_id,
        nombre=data.nombre,
        monto=data.monto,
        moneda=moneda_enum,
        periodo=periodo_enum,
        renovacion=renovacion_enum,
        estado=EstadoPresupuesto.ACTIVO
    )
    db.add(nuevo_presupuesto)
    db.flush()
    
    # 2. Crear registros PresupuestoCategoria
    for cat_data in data.categorias:
        pc = PresupuestoCategoria(
            presupuesto_id=nuevo_presupuesto.id,
            categoria_id=cat_data.categoria_id,
            subcategoria_id=cat_data.subcategoria_id
        )
        db.add(pc)
    
    # 3. Calcular fechas del primer periodo
    fecha_inicio, fecha_fin = calcular_fechas_periodo(data.periodo.value if hasattr(data.periodo, "value") else str(data.periodo), date.today())
    
    # 4. Calcular monto_usado inicial
    monto_usado = calcular_gasto_en_periodo(
        db, usuario_id, data.categorias, fecha_inicio, fecha_fin, moneda=moneda_enum
    )
    
    # 5. Crear PeriodoPresupuesto
    periodo = PeriodoPresupuesto(
        presupuesto_id=nuevo_presupuesto.id,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        monto_limite=data.monto,
        monto_usado=monto_usado,
        superado=monto_usado > data.monto
    )
    db.add(periodo)
    db.commit()
    
    # 6. Cargar presupuesto completo con relaciones
    presupuesto_cargado = obtener_presupuesto(db, usuario_id, nuevo_presupuesto.id)

    # 7. Verificar alertas iniciales
    if monto_usado > 0:
        verificar_alertas_presupuesto(db, presupuesto_cargado, periodo)
        
    return presupuesto_cargado

def obtener_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> Presupuesto:
    query = (
        select(Presupuesto)
        .options(
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.categoria),
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria),
            selectinload(Presupuesto.periodos)
        )
        .where(Presupuesto.id == id, Presupuesto.usuario_id == usuario_id)
    )
    presupuesto = db.execute(query).scalar_one_or_none()
    if not presupuesto:
        raise HTTPException(status_code=404, detail="No encontramos ese presupuesto.")
    return presupuesto

def actualizar_presupuesto(db: Session, usuario_id: UUID, id: UUID, data: PresupuestoUpdate) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    
    if data.nombre is not None:
        presupuesto.nombre = data.nombre
    if data.moneda is not None:
        try:
            presupuesto.moneda = Moneda(data.moneda)
        except ValueError:
            raise HTTPException(status_code=400, detail="Moneda inválida")
    if data.renovacion is not None:
        try:
            presupuesto.renovacion = RenovacionPresupuesto(data.renovacion)
        except ValueError:
            raise HTTPException(status_code=400, detail="Tipo de renovación inválido")
        
    periodo_cambio = False
    if data.periodo is not None and data.periodo != (presupuesto.periodo.value if hasattr(presupuesto.periodo, "value") else str(presupuesto.periodo)):
        try:
            presupuesto.periodo = PeriodoPresupuestoTipo(data.periodo)
            periodo_cambio = True
        except ValueError:
            raise HTTPException(status_code=400, detail="Periodo inválido")

    recalcular_monto = data.moneda is not None or periodo_cambio

    if data.categorias is not None:
        _validar_categorias_presupuesto(db, data.categorias)
        from sqlalchemy import delete
        db.execute(delete(PresupuestoCategoria).where(PresupuestoCategoria.presupuesto_id == id))
        for cat_data in data.categorias:
            pc = PresupuestoCategoria(
                presupuesto_id=id,
                categoria_id=cat_data.categoria_id,
                subcategoria_id=cat_data.subcategoria_id
            )
            db.add(pc)
        recalcular_monto = True
        
    periodo_actual = obtener_periodo_activo(db, presupuesto)

    if data.monto is not None:
        if data.monto <= 0:
            raise HTTPException(status_code=400, detail="Monto límite debe ser positivo")
        presupuesto.monto = data.monto
        if periodo_actual:
            periodo_actual.monto_limite = data.monto
            periodo_actual.superado = periodo_actual.monto_usado > periodo_actual.monto_limite
            
    if periodo_cambio and periodo_actual:
        fecha_inicio, fecha_fin = calcular_fechas_periodo(presupuesto.periodo.value, date.today())
        periodo_actual.fecha_inicio = fecha_inicio
        periodo_actual.fecha_fin = fecha_fin

    if recalcular_monto and periodo_actual:
        cats_to_calc = data.categorias if data.categorias is not None else presupuesto.categorias
        periodo_actual.monto_usado = calcular_gasto_en_periodo(
            db, usuario_id, cats_to_calc, periodo_actual.fecha_inicio, periodo_actual.fecha_fin, moneda=presupuesto.moneda
        )
        periodo_actual.superado = periodo_actual.monto_usado > periodo_actual.monto_limite

    db.commit()
    
    presupuesto_cargado = obtener_presupuesto(db, usuario_id, id)
    periodo_actual_cargado = obtener_periodo_activo(db, presupuesto_cargado)

    if (data.monto is not None or recalcular_monto) and periodo_actual_cargado:
        verificar_alertas_presupuesto(db, presupuesto_cargado, periodo_actual_cargado)
            
    return presupuesto_cargado

def pausar_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    presupuesto.estado = EstadoPresupuesto.PAUSADO
    db.commit()
    return obtener_presupuesto(db, usuario_id, id)

def reanudar_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    presupuesto.estado = EstadoPresupuesto.ACTIVO
    
    hoy = date.today()
    periodo_actual = obtener_periodo_activo(db, presupuesto)
    
    if not periodo_actual:
        periodo_val = presupuesto.periodo.value if hasattr(presupuesto.periodo, "value") else str(presupuesto.periodo)
        fecha_inicio, fecha_fin = calcular_fechas_periodo(periodo_val, hoy)
        monto_usado = calcular_gasto_en_periodo(
            db, usuario_id, presupuesto.categorias, fecha_inicio, fecha_fin, moneda=presupuesto.moneda
        )
        nuevo_periodo = PeriodoPresupuesto(
            presupuesto_id=presupuesto.id,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            monto_limite=presupuesto.monto,
            monto_usado=monto_usado,
            superado=monto_usado > presupuesto.monto
        )
        db.add(nuevo_periodo)
        
    db.commit()
    return obtener_presupuesto(db, usuario_id, id)

def eliminar_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> None:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    presupuesto.estado = EstadoPresupuesto.FINALIZADO
    db.commit()

def obtener_historial(db: Session, usuario_id: UUID, presupuesto_id: UUID) -> List[PeriodoPresupuesto]:
    query = (
        select(PeriodoPresupuesto)
        .where(
            PeriodoPresupuesto.presupuesto_id == presupuesto_id,
            Presupuesto.usuario_id == usuario_id
        )
        .join(Presupuesto)
        .order_by(desc(PeriodoPresupuesto.fecha_inicio))
    )
    return db.execute(query).scalars().all()

def registrar_impacto_presupuesto(db: Session, transaccion: Transaccion, revertir: bool = False):
    if transaccion.tipo != TipoTransaccion.EGRESO:
        return
    if transaccion.estado_verificacion not in [EstadoVerificacionTransaccion.CONFIRMADA, None]:
        return
    if transaccion.es_padre_cuotas:
        return

    presupuestos = db.execute(
        select(Presupuesto)
        .options(
            selectinload(Presupuesto.periodos),
            selectinload(Presupuesto.categorias)
        )
        .where(
            Presupuesto.usuario_id == transaccion.usuario_id,
            Presupuesto.estado == EstadoPresupuesto.ACTIVO
        )
    ).scalars().all()
    
    # Resolver categoría si solo tiene subcategoría
    tx_cat_id = transaccion.categoria_id
    if not tx_cat_id and transaccion.subcategoria_id:
        sub = db.get(Subcategoria, transaccion.subcategoria_id)
        if sub:
            tx_cat_id = sub.categoria_id

    for presu in presupuestos:
        if transaccion.moneda != presu.moneda:
            continue
            
        aplica = False
        for c in presu.categorias:
            if c.subcategoria_id is not None:
                if transaccion.subcategoria_id == c.subcategoria_id:
                    aplica = True
                    break
            elif c.categoria_id is not None:
                if tx_cat_id == c.categoria_id:
                    aplica = True
                    break
        
        if not aplica:
            continue
            
        periodo_activo = obtener_periodo_activo(db, presu)
        if not periodo_activo:
            continue
            
        if not (periodo_activo.fecha_inicio <= transaccion.fecha <= periodo_activo.fecha_fin):
            continue
            
        if not revertir:
            periodo_activo.monto_usado += transaccion.monto
        else:
            periodo_activo.monto_usado -= transaccion.monto
            periodo_activo.monto_usado = max(Decimal("0"), periodo_activo.monto_usado)
            
        periodo_activo.superado = periodo_activo.monto_usado > periodo_activo.monto_limite
        db.flush()
        
        if not revertir:
            verificar_alertas_presupuesto(db, presu, periodo_activo)

def verificar_alertas_presupuesto(db: Session, presupuesto: Presupuesto, periodo: PeriodoPresupuesto):
    if periodo.monto_limite == 0:
        return
        
    porcentaje = (periodo.monto_usado / periodo.monto_limite) * 100
    
    tipo = None
    if porcentaje >= 100:
        tipo = TipoNotificacion.PRESUPUESTO_AGOTADO
    elif porcentaje >= 80:
        tipo = TipoNotificacion.PRESUPUESTO_LIMITE
        
    if not tipo:
        return
        
    # 1. Obtener la configuración del usuario
    from app.services.notificacion_service import obtener_configuracion, crear_notificacion
    config = obtener_configuracion(db, presupuesto.usuario_id)

    # 2. Verificar si el canal/tipo está activo en la configuración del usuario
    canal_web = True
    canal_whatsapp = False

    if tipo == TipoNotificacion.PRESUPUESTO_AGOTADO:
        canal_web = config.presupuesto_umbral_2_web
        canal_whatsapp = config.presupuesto_umbral_2_whatsapp
    elif tipo == TipoNotificacion.PRESUPUESTO_LIMITE:
        if not config.presupuesto_umbral_1_activo:
            return  # Alerta 80% desactivada por completo
        canal_web = config.presupuesto_umbral_1_web
        canal_whatsapp = config.presupuesto_umbral_1_whatsapp

    # Si ambos canales están desactivados, no hacemos nada
    if not canal_web and not canal_whatsapp:
        return

    # 3. Formatear nombres de las categorías para el mensaje
    nombres_cats = ", ".join(set([c.subcategoria.nombre if c.subcategoria else (c.categoria.nombre if c.categoria else "") for c in presupuesto.categorias]))
    
    if tipo == TipoNotificacion.PRESUPUESTO_AGOTADO:
        mensaje = f"Llevás {formatear_monto(periodo.monto_usado)} de {formatear_monto(periodo.monto_limite)} en {nombres_cats}. Ya superaste el límite."
    else:
        mensaje = f"Llevás {formatear_monto(periodo.monto_usado)} de {formatear_monto(periodo.monto_limite)}."

    # 4. Crear la notificación utilizando el servicio común (se encarga del commit/db.add/deduplicación)
    notif = crear_notificacion(
        db=db,
        usuario_id=presupuesto.usuario_id,
        tipo=tipo,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje=mensaje,
        entidad_tipo="presupuesto",
        entidad_id=presupuesto.id,
        deep_link=f"/presupuestos/{presupuesto.id}",
        canal_web=canal_web,
        canal_whatsapp=canal_whatsapp,
        canal_email=False,
        grupo_agrupacion_override=f"presupuestos/{presupuesto.id}/{periodo.id}"
    )
    
    # 5. Enviar mensaje de WhatsApp inmediato si corresponde
    if notif and canal_whatsapp:
        usuario = db.get(Usuario, presupuesto.usuario_id)
        if usuario and usuario.telefono:
            try:
                enviar_mensaje_whatsapp(usuario.telefono, mensaje)
            except Exception:
                logger.warning("Error al enviar notificación de presupuesto por WhatsApp", exc_info=True)

def renovar_presupuestos(db: Session):
    hoy = date.today()
    presupuestos = db.execute(
        select(Presupuesto)
        .options(selectinload(Presupuesto.periodos), selectinload(Presupuesto.categorias))
        .where(
            Presupuesto.estado == EstadoPresupuesto.ACTIVO,
            Presupuesto.renovacion == RenovacionPresupuesto.AUTOMATICA
        )
    ).scalars().all()
    
    for presu in presupuestos:
        periodo_actual = obtener_periodo_activo(db, presu)
        if not periodo_actual:
            ultimo_periodo = max(presu.periodos, key=lambda p: p.fecha_fin) if presu.periodos else None
            
            if ultimo_periodo and ultimo_periodo.fecha_fin < hoy:
                ultimos_3 = db.execute(
                    select(PeriodoPresupuesto)
                    .where(PeriodoPresupuesto.presupuesto_id == presu.id)
                    .order_by(desc(PeriodoPresupuesto.fecha_fin))
                    .limit(3)
                ).scalars().all()
                
                if len(ultimos_3) == 3 and all(p.superado for p in ultimos_3):
                    promedio = sum(p.monto_usado for p in ultimos_3) / 3
                    from app.services.notificacion_service import crear_notificacion
                    mensaje_sug = f"Superaste el límite del presupuesto '{presu.nombre}' por 3 períodos seguidos. El gasto promedio real fue de {formatear_monto(promedio)}. Considerá ajustar el límite."
                    crear_notificacion(
                        db=db,
                        usuario_id=presu.usuario_id,
                        tipo=TipoNotificacion.PRESUPUESTO_LIMITE,
                        nivel=NivelNotificacion.SOFT,
                        mensaje=mensaje_sug,
                        entidad_tipo="presupuesto",
                        entidad_id=presu.id,
                        deep_link=f"/presupuestos/{presu.id}",
                        canal_web=True,
                        canal_whatsapp=False,
                        canal_email=False,
                        grupo_agrupacion_override=f"presupuestos/sugerencia/{presu.id}/{hoy.strftime('%Y%m%d')}"
                    )
                
                nueva_inicio, nueva_fin = calcular_fechas_periodo(presu.periodo, hoy)
                ya_existe = any(p.fecha_inicio == nueva_inicio for p in presu.periodos)
                if not ya_existe:
                    nuevo_periodo = PeriodoPresupuesto(
                        presupuesto_id=presu.id,
                        fecha_inicio=nueva_inicio,
                        fecha_fin=nueva_fin,
                        monto_limite=presu.monto,
                        monto_usado=Decimal("0"),
                        superado=False
                    )
                    db.add(nuevo_periodo)
    
    db.commit()
