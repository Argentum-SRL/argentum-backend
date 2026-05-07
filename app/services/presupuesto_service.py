from uuid import UUID
from datetime import date, datetime, timedelta, timezone
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
from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.usuario import Usuario
from app.schemas.presupuesto import PresupuestoCreate, PresupuestoUpdate
from app.services.whatsapp_service import enviar_mensaje_whatsapp

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
    fecha_fin: date
) -> Decimal:
    # categorías_input puede ser PresupuestoCategoriaInput (schema) o PresupuestoCategoria (modelo)
    cat_ids = [c.categoria_id for c in categorias_input if c.categoria_id]
    subcat_ids = [c.subcategoria_id for c in categorias_input if c.subcategoria_id]
    
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
    
    conditions = []
    if cat_ids:
        conditions.append(Transaccion.categoria_id.in_(cat_ids))
    if subcat_ids:
        conditions.append(Transaccion.subcategoria_id.in_(subcat_ids))
        
    if not conditions:
        return Decimal("0")
        
    query = query.where(or_(*conditions))
    
    resultado = db.execute(query).scalar()
    return resultado if resultado else Decimal("0")

def obtener_periodo_activo(db: Session, presupuesto: Presupuesto) -> Optional[PeriodoPresupuesto]:
    hoy = date.today()
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
    # 2. Crear registro Presupuesto
    nuevo_presupuesto = Presupuesto(
        usuario_id=usuario_id,
        nombre=data.nombre,
        monto=data.monto,
        moneda=data.moneda,
        periodo=data.periodo,
        renovacion=data.renovacion,
        estado=EstadoPresupuesto.ACTIVO
    )
    db.add(nuevo_presupuesto)
    db.flush()
    
    # 3. Crear registros PresupuestoCategoria
    for cat_data in data.categorias:
        pc = PresupuestoCategoria(
            presupuesto_id=nuevo_presupuesto.id,
            categoria_id=cat_data.categoria_id,
            subcategoria_id=cat_data.subcategoria_id
        )
        db.add(pc)
    
    # 4. Calcular fechas del primer periodo
    fecha_inicio, fecha_fin = calcular_fechas_periodo(data.periodo, date.today())
    
    # 5. Calcular monto_usado inicial
    monto_usado = calcular_gasto_en_periodo(
        db, usuario_id, data.categorias, fecha_inicio, fecha_fin
    )
    
    # 6. Crear PeriodoPresupuesto
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
    
    # 7. Verificar alertas iniciales
    if monto_usado > 0:
        db.refresh(nuevo_presupuesto)
        verificar_alertas_presupuesto(db, nuevo_presupuesto, periodo)
        
    db.refresh(nuevo_presupuesto)
    return nuevo_presupuesto

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
        raise HTTPException(status_code=404, detail="Presupuesto no encontrado")
    return presupuesto

def actualizar_presupuesto(db: Session, usuario_id: UUID, id: UUID, data: PresupuestoUpdate) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    
    if data.nombre is not None:
        presupuesto.nombre = data.nombre
    if data.moneda is not None:
        presupuesto.moneda = data.moneda
    if data.renovacion is not None:
        presupuesto.renovacion = data.renovacion
        
    recalcular_monto = False
    if data.categorias is not None:
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
        
    if data.monto is not None:
        presupuesto.monto = data.monto
        periodo_actual = obtener_periodo_activo(db, presupuesto)
        if periodo_actual:
            periodo_actual.monto_limite = data.monto
            periodo_actual.superado = periodo_actual.monto_usado > periodo_actual.monto_limite
            
    if recalcular_monto:
        periodo_actual = obtener_periodo_activo(db, presupuesto)
        if periodo_actual:
            periodo_actual.monto_usado = calcular_gasto_en_periodo(
                db, usuario_id, data.categorias, periodo_actual.fecha_inicio, periodo_actual.fecha_fin
            )
            periodo_actual.superado = periodo_actual.monto_usado > periodo_actual.monto_limite

    db.commit()
    db.refresh(presupuesto)
    
    if data.monto is not None or recalcular_monto:
        periodo_actual = obtener_periodo_activo(db, presupuesto)
        if periodo_actual:
            verificar_alertas_presupuesto(db, presupuesto, periodo_actual)
            
    return presupuesto

def pausar_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    presupuesto.estado = EstadoPresupuesto.PAUSADO
    db.commit()
    db.refresh(presupuesto)
    return presupuesto

def reanudar_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    presupuesto.estado = EstadoPresupuesto.ACTIVO
    
    hoy = date.today()
    periodo_actual = obtener_periodo_activo(db, presupuesto)
    
    if not periodo_actual:
        fecha_inicio, fecha_fin = calcular_fechas_periodo(presupuesto.periodo, hoy)
        monto_usado = calcular_gasto_en_periodo(
            db, usuario_id, presupuesto.categorias, fecha_inicio, fecha_fin
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
    db.refresh(presupuesto)
    return presupuesto

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
    
    for presu in presupuestos:
        aplica = any(
            (c.categoria_id == transaccion.categoria_id and c.categoria_id is not None) or
            (c.subcategoria_id == transaccion.subcategoria_id and c.subcategoria_id is not None)
            for c in presu.categorias
        )
        
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
        tipo = TipoNotificacion.PRESUPUESTO_100
    elif porcentaje >= 80:
        tipo = TipoNotificacion.PRESUPUESTO_80
        
    if not tipo:
        return
        
    modulo_ref_full = f"presupuestos/{presupuesto.id}/{periodo.id}"
    existe = db.execute(
        select(Notificacion).where(
            Notificacion.usuario_id == presupuesto.usuario_id,
            Notificacion.tipo == tipo,
            Notificacion.modulo_ref == modulo_ref_full
        )
    ).scalars().first()
    
    if existe:
        return

    nombres_cats = ", ".join(set([c.categoria.nombre if c.categoria else c.subcategoria.nombre for c in presupuesto.categorias]))
    
    if tipo == TipoNotificacion.PRESUPUESTO_100:
        titulo = f"Superaste el presupuesto de {presupuesto.nombre}"
        mensaje = f"Llevás {formatear_monto(periodo.monto_usado)} de {formatear_monto(periodo.monto_limite)} en {nombres_cats}. Ya superaste el límite."
    else:
        titulo = f"Vas por el {porcentaje:.0f}% de {presupuesto.nombre}"
        mensaje = f"Llevás {formatear_monto(periodo.monto_usado)} de {formatear_monto(periodo.monto_limite)}."

    notif = Notificacion(
        usuario_id=presupuesto.usuario_id,
        tipo=tipo,
        titulo=titulo,
        mensaje=mensaje,
        modulo_ref=modulo_ref_full
    )
    db.add(notif)
    
    config = db.execute(
        select(ConfiguracionNotificacion).where(
            ConfiguracionNotificacion.usuario_id == presupuesto.usuario_id,
            ConfiguracionNotificacion.tipo == tipo
        )
    ).scalar_one_or_none()
    
    if config and config.canal_wpp:
        usuario = db.get(Usuario, presupuesto.usuario_id)
        if usuario and usuario.telefono:
            enviar_mensaje_whatsapp(usuario.telefono, mensaje)

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
                    notif = Notificacion(
                        usuario_id=presu.usuario_id,
                        tipo=TipoNotificacion.SUGERENCIA_PRESUPUESTO,
                        titulo=f"Revisá el presupuesto de {presu.nombre}",
                        mensaje=f"Superaste el limite 3 periodos seguidos. El gasto promedio real fue de {formatear_monto(promedio)}. Considerá ajustar el límite.",
                        modulo_ref=f"presupuestos/{presu.id}"
                    )
                    db.add(notif)
                
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
