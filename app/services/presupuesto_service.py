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
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)

def _validar_categorias_presupuesto(db: Session, categorias_input: List, usuario_id: Optional[UUID] = None) -> None:
    if not categorias_input:
        raise HTTPException(status_code=400, detail="Debe seleccionar al menos una categoría")

    cat_ids = {c.categoria_id for c in categorias_input if c.categoria_id}
    subcat_ids = {c.subcategoria_id for c in categorias_input if getattr(c, 'subcategoria_id', None)}

    if cat_ids:
        query = select(Categoria).where(Categoria.id.in_(cat_ids))
        cats = db.execute(query).scalars().all()
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
        query = select(Subcategoria).options(joinedload(Subcategoria.categoria)).where(Subcategoria.id.in_(subcat_ids))
        subs = db.execute(query).scalars().all()
        found_subcat_ids = {s.id for s in subs}
        if len(found_subcat_ids) != len(subcat_ids):
            raise HTTPException(status_code=404, detail="Categoría no encontrada")
        for sub in subs:
            if sub.categoria and sub.categoria.tipo != TipoCategoria.EGRESO:
                raise HTTPException(
                    status_code=400, 
                    detail="Los presupuestos solo pueden asociarse a categorías de egreso"
                )
        for c in categorias_input:
            if c.categoria_id and getattr(c, 'subcategoria_id', None):
                sub = next((s for s in subs if s.id == c.subcategoria_id), None)
                if sub and sub.categoria_id != c.categoria_id:
                    raise HTTPException(
                        status_code=400,
                        detail="La subcategoría seleccionada no pertenece a la categoría indicada"
                    )

from app.utils.formato import formatear_monto

def calcular_fechas_periodo(
    periodo: str | PeriodoPresupuestoTipo, 
    fecha_referencia: date, 
    usuario: Optional[Usuario] = None
) -> tuple[date, date]:
    periodo_str = periodo.value if hasattr(periodo, "value") else str(periodo)
    year = fecha_referencia.year
    month = fecha_referencia.month
    
    if periodo_str == PeriodoPresupuestoTipo.MENSUAL.value:
        if usuario is not None:
            from app.services.dashboard_service import get_ciclo_fechas
            return get_ciclo_fechas(usuario, fecha_referencia)
        fecha_inicio = date(year, month, 1)
        fecha_fin = date(year, month, calendar.monthrange(year, month)[1])
        return fecha_inicio, fecha_fin
        
    if periodo_str == PeriodoPresupuestoTipo.QUINCENAL.value:
        if fecha_referencia.day <= 15:
            fecha_inicio = date(year, month, 1)
            fecha_fin = date(year, month, 15)
        else:
            fecha_inicio = date(year, month, 16)
            fecha_fin = date(year, month, calendar.monthrange(year, month)[1])
        return fecha_inicio, fecha_fin
        
    if periodo_str == PeriodoPresupuestoTipo.SEMANAL.value:
        weekday = fecha_referencia.weekday() # 0=Lunes
        fecha_inicio = fecha_referencia - timedelta(days=weekday)
        fecha_fin = fecha_inicio + timedelta(days=6)
        return fecha_inicio, fecha_fin
    
    raise ValueError(f"Periodo inválido: {periodo}")

class GastoPresupuesto(Decimal):
    """
    Subclase de Decimal que encapsula el gasto total del presupuesto manteniendo
    total compatibilidad numérica hacia atrás con cualquier consumidor existente
    (comparaciones, float(), sumas, formateos, etc.), y exponiendo además el
    desglose por moneda y montos sin cotización.
    """
    gasto_total: Decimal
    monto_propio: Decimal
    monto_convertido: Decimal
    monto_sin_cotizacion: Decimal
    moneda_sin_cotizacion: Optional[str]

    def __new__(
        cls, 
        total: Decimal | str | float | int, 
        propio: Decimal | str | float | int = Decimal("0"), 
        convertido: Decimal | str | float | int = Decimal("0"), 
        sin_cotizacion: Decimal | str | float | int = Decimal("0"), 
        moneda_sin_cotizacion: Optional[str] = None
    ):
        dec_total = Decimal(str(total)).quantize(Decimal("0.01"))
        instance = super().__new__(cls, dec_total)
        instance.gasto_total = dec_total
        instance.monto_propio = Decimal(str(propio)).quantize(Decimal("0.01"))
        instance.monto_convertido = Decimal(str(convertido)).quantize(Decimal("0.01"))
        instance.monto_sin_cotizacion = Decimal(str(sin_cotizacion)).quantize(Decimal("0.01"))
        instance.moneda_sin_cotizacion = moneda_sin_cotizacion
        return instance


def calcular_gasto_en_periodo(
    db: Session, 
    usuario_id: UUID, 
    categorias_input: List, 
    fecha_inicio: date, 
    fecha_fin: date,
    moneda: Optional[Moneda | str] = None,
    usuario: Optional[Usuario] = None
) -> GastoPresupuesto:
    # categorías_input puede ser PresupuestoCategoriaInput (schema) o PresupuestoCategoria (modelo)
    # Regla: Si tiene subcategoria_id especificado, aplica solo a esa subcategoría.
    # Si tiene categoria_id pero no subcategoria_id, aplica a toda la categoría padre.
    cat_ids = [c.categoria_id for c in categorias_input if c.categoria_id and not getattr(c, 'subcategoria_id', None)]
    subcat_ids = [c.subcategoria_id for c in categorias_input if getattr(c, 'subcategoria_id', None)]
    
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
        return GastoPresupuesto(Decimal("0"))

    # Normalizar moneda del presupuesto
    presu_moneda_str = "ARS"
    if moneda is not None:
        moneda_enum = moneda if isinstance(moneda, Moneda) else Moneda(moneda)
        presu_moneda_str = moneda_enum.value if hasattr(moneda_enum, "value") else str(moneda_enum)

    # 1. Consultar transacciones en TODAS las monedas en una única consulta
    query = select(
        Transaccion.monto,
        Transaccion.moneda,
        Transaccion.fecha,
        Transaccion.cotizacion_aplicada
    ).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        or_(
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.estado_verificacion == None
        ),
        Transaccion.es_padre_cuotas == False,
        Transaccion.fecha >= fecha_inicio,
        Transaccion.fecha <= fecha_fin,
        or_(*conditions)
    )
    
    rows = db.execute(query).all()
    
    monto_propio = Decimal("0")
    monto_convertido = Decimal("0")
    monto_sin_cotizacion = Decimal("0")
    moneda_sin_cotizacion = None
    
    txs_otra_moneda = []
    for r in rows:
        r_moneda_str = r.moneda.value if hasattr(r.moneda, "value") else str(r.moneda)
        if r_moneda_str == presu_moneda_str:
            monto_propio += Decimal(str(r.monto))
        else:
            txs_otra_moneda.append(r)

    # Si hay transacciones en otra moneda, resolver cotizaciones por bloque (máximo 1 consulta adicional)
    if txs_otra_moneda:
        fechas_necesarias = {
            r.fecha for r in txs_otra_moneda 
            if not (r.cotizacion_aplicada and r.cotizacion_aplicada > 0)
        }
        mapa_cotizaciones = {}
        if fechas_necesarias:
            if usuario is None:
                usuario = db.get(Usuario, usuario_id)
            tipo_dolar = getattr(usuario, "tipo_dolar", "blue") or "blue"
            from app.services.dolar_service import obtener_cotizaciones_por_fechas
            mapa_cotizaciones = obtener_cotizaciones_por_fechas(db, tipo_dolar, fechas_necesarias)

        for r in txs_otra_moneda:
            tx_monto = Decimal(str(r.monto))
            tx_moneda_str = r.moneda.value if hasattr(r.moneda, "value") else str(r.moneda)

            cotizacion = None
            if r.cotizacion_aplicada and r.cotizacion_aplicada > 0:
                cotizacion = Decimal(str(r.cotizacion_aplicada))
            else:
                cot_obj = mapa_cotizaciones.get(r.fecha)
                if cot_obj is not None:
                    cot_val = cot_obj.promedio or cot_obj.venta or cot_obj.compra
                    if cot_val and cot_val > Decimal("0"):
                        cotizacion = Decimal(str(cot_val))

            if cotizacion is None or cotizacion <= Decimal("0"):
                monto_sin_cotizacion += tx_monto
                moneda_sin_cotizacion = tx_moneda_str
                logger.info(
                    "Gasto de %s %s del %s no contabilizado en presupuesto: sin cotización disponible.",
                    tx_monto, tx_moneda_str, r.fecha
                )
                continue

            # Dirección de conversión documentada:
            # Si el presupuesto es en ARS y el gasto es en USD -> se MULTIPLICA (USD * cotizacion = ARS)
            # Si el presupuesto es en USD y el gasto es en ARS -> se DIVIDE (ARS / cotizacion = USD)
            if presu_moneda_str == "ARS" and tx_moneda_str == "USD":
                conv = (tx_monto * cotizacion).quantize(Decimal("0.01"))
            elif presu_moneda_str == "USD" and tx_moneda_str == "ARS":
                conv = (tx_monto / cotizacion).quantize(Decimal("0.01"))
            else:
                conv = tx_monto

            monto_convertido += conv

    total = monto_propio + monto_convertido
    return GastoPresupuesto(
        total,
        propio=monto_propio,
        convertido=monto_convertido,
        sin_cotizacion=monto_sin_cotizacion,
        moneda_sin_cotizacion=moneda_sin_cotizacion
    )

def obtener_periodo_activo(db: Optional[Session], presupuesto: Presupuesto) -> Optional[PeriodoPresupuesto]:
    hoy = hoy_argentina()
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
            selectinload(Presupuesto.periodos),
            joinedload(Presupuesto.usuario)
        )
        .where(Presupuesto.usuario_id == usuario_id)
    )
    
    if estado:
        query = query.where(Presupuesto.estado == estado)
        
    presupuestos = db.execute(query).scalars().all()
    for presu in presupuestos:
        periodo_activo = obtener_periodo_activo(db, presu)
        if periodo_activo:
            gasto = calcular_gasto_en_periodo(
                db, usuario_id, presu.categorias,
                periodo_activo.fecha_inicio, periodo_activo.fecha_fin,
                moneda=presu.moneda, usuario=presu.usuario
            )
            periodo_activo.monto_usado = gasto
            periodo_activo.superado = gasto > periodo_activo.monto_limite

    return presupuestos

def crear_presupuesto(db: Session, usuario_id: UUID, data: PresupuestoCreate) -> Presupuesto:
    if data.monto <= 0:
        raise HTTPException(status_code=400, detail="Monto límite debe ser positivo")

    _validar_categorias_presupuesto(db, data.categorias, usuario_id)

    nombre_limpio = data.nombre.strip()
    nombre_existente = db.execute(
        select(Presupuesto.id).where(
            Presupuesto.usuario_id == usuario_id,
            func.lower(Presupuesto.nombre) == nombre_limpio.lower(),
            Presupuesto.estado.in_([EstadoPresupuesto.ACTIVO, EstadoPresupuesto.PAUSADO])
        )
    ).scalar_one_or_none()
    if nombre_existente:
        raise HTTPException(
            status_code=400,
            detail="Ya tenés un presupuesto activo o pausado con ese nombre"
        )

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
        nombre=nombre_limpio,
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
    usuario = db.get(Usuario, usuario_id)
    fecha_inicio, fecha_fin = calcular_fechas_periodo(data.periodo, hoy_argentina(), usuario=usuario)
    
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
            joinedload(Presupuesto.usuario),
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.categoria),
            selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria),
            selectinload(Presupuesto.periodos)
        )
        .where(Presupuesto.id == id, Presupuesto.usuario_id == usuario_id)
    )
    presupuesto = db.execute(query).scalar_one_or_none()
    if not presupuesto:
        raise HTTPException(status_code=404, detail="No encontramos ese presupuesto.")
    pa = obtener_periodo_activo(db, presupuesto)
    if pa:
        gasto = calcular_gasto_en_periodo(
            db, usuario_id, presupuesto.categorias,
            pa.fecha_inicio, pa.fecha_fin,
            moneda=presupuesto.moneda, usuario=presupuesto.usuario
        )
        pa.monto_usado = gasto
        pa.superado = gasto > pa.monto_limite
    return presupuesto

def actualizar_presupuesto(db: Session, usuario_id: UUID, id: UUID, data: PresupuestoUpdate) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    
    if data.nombre is not None:
        nombre_limpio = data.nombre.strip()
        nombre_existente = db.execute(
            select(Presupuesto.id).where(
                Presupuesto.usuario_id == usuario_id,
                func.lower(Presupuesto.nombre) == nombre_limpio.lower(),
                Presupuesto.estado.in_([EstadoPresupuesto.ACTIVO, EstadoPresupuesto.PAUSADO]),
                Presupuesto.id != id
            )
        ).scalar_one_or_none()
        if nombre_existente:
            raise HTTPException(
                status_code=400,
                detail="Ya tenés un presupuesto activo o pausado con ese nombre"
            )
        presupuesto.nombre = nombre_limpio
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
        _validar_categorias_presupuesto(db, data.categorias, usuario_id)
        presupuesto.categorias.clear()
        for cat_data in data.categorias:
            pc = PresupuestoCategoria(
                presupuesto_id=id,
                categoria_id=cat_data.categoria_id,
                subcategoria_id=cat_data.subcategoria_id
            )
            presupuesto.categorias.append(pc)
        recalcular_monto = True
        
    periodo_actual = obtener_periodo_activo(db, presupuesto)

    if data.monto is not None:
        if data.monto <= 0:
            raise HTTPException(status_code=400, detail="Monto límite debe ser positivo")
        presupuesto.monto = data.monto
        if periodo_actual:
            periodo_actual.monto_limite = data.monto
            periodo_actual.superado = periodo_actual.monto_usado > data.monto
            
    if periodo_cambio and periodo_actual:
        fecha_inicio, fecha_fin = calcular_fechas_periodo(presupuesto.periodo, hoy_argentina(), usuario=presupuesto.usuario)
        periodo_actual.fecha_inicio = fecha_inicio
        periodo_actual.fecha_fin = fecha_fin

    if recalcular_monto and periodo_actual:
        periodo_actual.monto_usado = calcular_gasto_en_periodo(
            db, usuario_id, presupuesto.categorias, periodo_actual.fecha_inicio, periodo_actual.fecha_fin, moneda=presupuesto.moneda
        )
        periodo_actual.superado = periodo_actual.monto_usado > periodo_actual.monto_limite

    db.commit()
    
    presupuesto_cargado = obtener_presupuesto(db, usuario_id, id)
    if periodo_actual and periodo_actual.monto_usado > 0:
        verificar_alertas_presupuesto(db, presupuesto_cargado, periodo_actual)
            
    return presupuesto_cargado

def pausar_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    presupuesto.estado = EstadoPresupuesto.PAUSADO
    db.commit()
    return obtener_presupuesto(db, usuario_id, id)

def reanudar_presupuesto(db: Session, usuario_id: UUID, id: UUID) -> Presupuesto:
    presupuesto = obtener_presupuesto(db, usuario_id, id)
    presupuesto.estado = EstadoPresupuesto.ACTIVO
    
    hoy = hoy_argentina()
    periodo_actual = obtener_periodo_activo(db, presupuesto)
    
    if not periodo_actual:
        fecha_inicio, fecha_fin = calcular_fechas_periodo(presupuesto.periodo, hoy, usuario=presupuesto.usuario)
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
    if presupuesto.estado == EstadoPresupuesto.FINALIZADO:
        db.delete(presupuesto)
    else:
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
            selectinload(Presupuesto.categorias),
            joinedload(Presupuesto.usuario)
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

        presu_moneda_str = presu.moneda.value if hasattr(presu.moneda, "value") else str(presu.moneda)
        tx_moneda_str = transaccion.moneda.value if hasattr(transaccion.moneda, "value") else str(transaccion.moneda)

        if tx_moneda_str == presu_moneda_str:
            monto_impacto = transaccion.monto
        else:
            cotizacion = None
            if transaccion.cotizacion_aplicada and transaccion.cotizacion_aplicada > 0:
                cotizacion = Decimal(str(transaccion.cotizacion_aplicada))
            else:
                from app.services.dolar_service import obtener_cotizacion_por_fecha
                usuario_presu = presu.usuario or db.get(Usuario, presu.usuario_id)
                tipo_dolar = getattr(usuario_presu, "tipo_dolar", "blue") or "blue"
                cot_obj = obtener_cotizacion_por_fecha(db, tipo_dolar, transaccion.fecha)
                if cot_obj:
                    cot_val = cot_obj.promedio or cot_obj.venta or cot_obj.compra
                    if cot_val and cot_val > Decimal("0"):
                        cotizacion = Decimal(str(cot_val))

            if not cotizacion or cotizacion <= Decimal("0"):
                continue

            if presu_moneda_str == "ARS" and tx_moneda_str == "USD":
                monto_impacto = (transaccion.monto * cotizacion).quantize(Decimal("0.01"))
            elif presu_moneda_str == "USD" and tx_moneda_str == "ARS":
                monto_impacto = (transaccion.monto / cotizacion).quantize(Decimal("0.01"))
            else:
                monto_impacto = transaccion.monto
            
        if not revertir:
            periodo_activo.monto_usado += monto_impacto
        else:
            periodo_activo.monto_usado -= monto_impacto
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
        mensaje = f"Llevás {formatear_monto(periodo.monto_usado, presupuesto.moneda)} de {formatear_monto(periodo.monto_limite, presupuesto.moneda)} en {nombres_cats}. Ya superaste el límite."
    else:
        mensaje = f"Llevás {formatear_monto(periodo.monto_usado, presupuesto.moneda)} de {formatear_monto(periodo.monto_limite, presupuesto.moneda)}."

    # 4. Crear la notificación utilizando el servicio común (se encarga del commit/db.add/deduplicación)
    notif = crear_notificacion(
        db=db,
        usuario_id=presupuesto.usuario_id,
        tipo=tipo,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje=mensaje,
        entidad_tipo="presupuesto",
        entidad_id=presupuesto.id,
        deep_link="/app/presupuestos",
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
    hoy = hoy_argentina()
    presupuestos = db.execute(
        select(Presupuesto)
        .options(
            selectinload(Presupuesto.periodos), 
            selectinload(Presupuesto.categorias),
            joinedload(Presupuesto.usuario)
        )
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
                    mensaje_sug = f"Superaste el límite del presupuesto '{presu.nombre}' por 3 períodos seguidos. El gasto promedio real fue de {formatear_monto(promedio, presu.moneda)}. Considerá ajustar el límite."
                    crear_notificacion(
                        db=db,
                        usuario_id=presu.usuario_id,
                        tipo=TipoNotificacion.PRESUPUESTO_LIMITE,
                        nivel=NivelNotificacion.SOFT,
                        mensaje=mensaje_sug,
                        entidad_tipo="presupuesto",
                        entidad_id=presu.id,
                        deep_link="/app/presupuestos",
                        canal_web=True,
                        canal_whatsapp=False,
                        canal_email=False,
                        grupo_agrupacion_override=f"presupuestos/sugerencia/{presu.id}/{hoy.strftime('%Y%m%d')}"
                    )
                
                nueva_inicio, nueva_fin = calcular_fechas_periodo(presu.periodo, hoy, usuario=presu.usuario)
                ya_existe = any(p.fecha_inicio == nueva_inicio for p in presu.periodos)
                if not ya_existe:
                    monto_usado = calcular_gasto_en_periodo(
                        db, presu.usuario_id, presu.categorias, nueva_inicio, nueva_fin, moneda=presu.moneda
                    )
                    nuevo_periodo = PeriodoPresupuesto(
                        presupuesto_id=presu.id,
                        fecha_inicio=nueva_inicio,
                        fecha_fin=nueva_fin,
                        monto_limite=presu.monto,
                        monto_usado=monto_usado,
                        superado=monto_usado > presu.monto
                    )
                    db.add(nuevo_periodo)
                    presu.periodos.append(nuevo_periodo)
    
    db.commit()
