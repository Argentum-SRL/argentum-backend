import logging
import statistics
import calendar as cal
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select, func, and_
from sqlalchemy.orm import joinedload, Session

from app.models.perfil_financiero import PerfilFinanciero
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.models.usuario import Usuario, Moneda
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion

from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.presupuesto import Presupuesto, EstadoPresupuesto
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.services.suscripcion_service import obtener_total_mensual

logger = logging.getLogger(__name__)


def obtener_cotizacion_dolar(usuario: Usuario) -> Decimal:
    """Obtiene la cotización preferida del usuario o un fallback de 1000.0."""
    try:
        from app.services.dolar_service import get_cotizaciones_dolar
        res = get_cotizaciones_dolar()
        tipo = usuario.tipo_dolar or "blue"
        if tipo == "bolsa":
            tipo = "mep"
        cotizacion = res.get("cotizaciones", {}).get(tipo, {}).get("promedio")
        if cotizacion:
            return Decimal(str(cotizacion))
    except Exception as e:
        logger.warning(f"Error al obtener cotización del dólar: {str(e)}")
    return Decimal("1000.0")


# --- IMPLEMENTACIONES SÍNCRONAS INTERNAS ---

def _calcular_tasa_ahorro_sync(db, usuario_id: UUID, fecha_inicio: date) -> Decimal | None:
    hoy = date.today()

    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        return None
    cotizacion = obtener_cotizacion_dolar(usuario)

    txs = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.es_padre_cuotas == False,
            Transaccion.fecha >= fecha_inicio,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    total_ingresos = Decimal("0")
    total_gastos = Decimal("0")
    tiene_ingreso = False

    for tx in txs:
        monto = tx.monto
        if tx.moneda == Moneda.USD:
            monto = tx.monto * cotizacion

        if tx.tipo == TipoTransaccion.INGRESO:
            total_ingresos += monto
            tiene_ingreso = True
        elif tx.tipo == TipoTransaccion.EGRESO:
            total_gastos += monto

    # Restricción: tasa_ahorro requiere al menos 1 ingreso en el período
    if not tiene_ingreso or total_ingresos <= 0:
        return None

    return (total_ingresos - total_gastos) / total_ingresos


def _calcular_score_impulsividad_sync(db, usuario_id: UUID, fecha_inicio: date) -> int | None:
    hoy = date.today()

    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        return None
    cotizacion = obtener_cotizacion_dolar(usuario)

    # Solo gastos (egresos) confirmados y no padres de cuotas
    gastos = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.es_padre_cuotas == False,
            Transaccion.fecha >= fecha_inicio,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    # Mínimo 20 transacciones para calcular score_impulsividad
    if len(gastos) < 20:
        return None

    montos_ars = []
    for g in gastos:
        monto = g.monto
        if g.moneda == Moneda.USD:
            monto = g.monto * cotizacion
        montos_ars.append(monto)

    montos_ars.sort()
    percentil_25 = montos_ars[len(montos_ars) // 4]
    count_pequeños = sum(1 for m in montos_ars if m <= percentil_25)

    score = round((count_pequeños / len(gastos)) * 100)
    return score


def _calcular_ratio_cuotas_sync(db, usuario_id: UUID, fecha_inicio: date) -> Decimal | None:
    hoy = date.today()
    primer_dia_mes = date(hoy.year, hoy.month, 1)
    ultimo_dia = cal.monthrange(hoy.year, hoy.month)[1]
    ultimo_dia_mes = date(hoy.year, hoy.month, ultimo_dia)

    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        return None
    cotizacion = obtener_cotizacion_dolar(usuario)

    # Cuotas no pagadas que vencen este mes
    cuotas = db.execute(
        select(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .options(joinedload(Cuota.grupo))
        .filter(
            GrupoCuotas.usuario_id == usuario_id,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento >= primer_dia_mes,
            Cuota.fecha_vencimiento <= ultimo_dia_mes
        )
    ).scalars().all()

    suma_cuotas = Decimal("0")
    for c in cuotas:
        monto = c.monto_real if c.monto_real is not None else c.monto_proyectado or Decimal("0")
        if c.grupo.moneda == Moneda.USD:
            monto = monto * cotizacion
        suma_cuotas += monto

    # Calcular promedios mensuales desde fecha_inicio
    txs = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.es_padre_cuotas == False,
            Transaccion.fecha >= fecha_inicio,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    total_ingresos = Decimal("0")
    total_gastos = Decimal("0")

    for tx in txs:
        monto = tx.monto
        if tx.moneda == Moneda.USD:
            monto = tx.monto * cotizacion

        if tx.tipo == TipoTransaccion.INGRESO:
            total_ingresos += monto
        elif tx.tipo == TipoTransaccion.EGRESO:
            total_gastos += monto

    cant_meses = Decimal(str(max(1.0, (hoy - fecha_inicio).days / 30.0)))
    ingreso_promedio_mensual = total_ingresos / cant_meses
    gasto_promedio_mensual = total_gastos / cant_meses

    denominador = ingreso_promedio_mensual
    if denominador == 0:
        denominador = gasto_promedio_mensual

    if denominador <= 0:
        return None

    return suma_cuotas / denominador


def _calcular_cumplimiento_presupuesto_sync(db, usuario_id: UUID, fecha_inicio: date) -> Decimal | None:
    presupuestos = db.execute(
        select(Presupuesto).where(
            Presupuesto.usuario_id == usuario_id,
            Presupuesto.estado == EstadoPresupuesto.ACTIVO
        )
    ).scalars().all()

    if not presupuestos:
        return None

    presupuesto_ids = [p.id for p in presupuestos]

    periodos = db.execute(
        select(PeriodoPresupuesto)
        .where(
            PeriodoPresupuesto.presupuesto_id.in_(presupuesto_ids),
            PeriodoPresupuesto.fecha_inicio >= fecha_inicio
        )
    ).scalars().all()

    if not periodos:
        return None

    ciclos_dentro_del_limite = sum(1 for p in periodos if not p.superado)
    total_ciclos_evaluados = len(periodos)

    return Decimal(str(ciclos_dentro_del_limite)) / Decimal(str(total_ciclos_evaluados))


def _calcular_consistencia_registro_sync(db, usuario_id: UUID, fecha_inicio: date) -> Decimal:
    hoy = date.today()

    primera_fecha = db.execute(
        select(func.min(Transaccion.fecha)).where(Transaccion.usuario_id == usuario_id)
    ).scalar()

    if not primera_fecha:
        return Decimal("0.0")

    dias = (hoy - fecha_inicio).days
    dias = max(1, dias)

    inicio_periodo = fecha_inicio

    dias_reales = (hoy - primera_fecha).days + 1
    dias_evaluados = min(dias, dias_reales)
    dias_evaluados = max(1, dias_evaluados)

    fechas_unicas = db.execute(
        select(func.distinct(Transaccion.fecha))
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.es_padre_cuotas == False,
            Transaccion.fecha >= inicio_periodo,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    dias_con_transacciones = len(fechas_unicas)
    consistencia = Decimal(str(dias_con_transacciones)) / Decimal(str(dias_evaluados))
    return min(Decimal("1.0"), consistencia)


def _calcular_porcentaje_suscripciones_sync(db, usuario_id: UUID, fecha_inicio: date) -> Decimal | None:
    suscripciones_data = obtener_total_mensual(db, usuario_id)
    total_ars_subs = Decimal(str(suscripciones_data.get("total_ars") or 0))
    total_usd_subs = Decimal(str(suscripciones_data.get("total_usd") or 0))

    if total_ars_subs == 0 and total_usd_subs == 0:
        return Decimal("0.0")

    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        return None
    cotizacion = obtener_cotizacion_dolar(usuario)

    hoy = date.today()
    gastos = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.es_padre_cuotas == False,
            Transaccion.fecha >= fecha_inicio,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    total_gastos = Decimal("0")
    for tx in gastos:
        monto = tx.monto
        if tx.moneda == Moneda.USD:
            monto = tx.monto * cotizacion
        total_gastos += monto

    cant_meses = Decimal(str(max(1.0, (hoy - fecha_inicio).days / 30.0)))
    gasto_promedio_mensual = total_gastos / cant_meses
    if gasto_promedio_mensual == 0:
        return None

    costo_mensual_suscripciones = total_ars_subs + total_usd_subs * cotizacion
    return costo_mensual_suscripciones / gasto_promedio_mensual


def _calcular_y_persistir_perfil_sync(db, usuario_id: UUID) -> PerfilFinanciero:
    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        raise ValueError(f"Usuario {usuario_id} no encontrado")

    hoy = date.today()
    try:
        dia_inicio = int(usuario.ciclo_valor) if usuario.ciclo_valor else 1
    except ValueError:
        dia_inicio = 1

    if hoy.day >= dia_inicio:
        ultimo_dia_mes = cal.monthrange(hoy.year, hoy.month)[1]
        dia_real = min(dia_inicio, ultimo_dia_mes)
        inicio_ciclo = hoy.replace(day=dia_real)
    else:
        mes_anterior = hoy.replace(day=1) - timedelta(days=1)
        ultimo_dia_mes = cal.monthrange(mes_anterior.year, mes_anterior.month)[1]
        dia_real = min(dia_inicio, ultimo_dia_mes)
        inicio_ciclo = mes_anterior.replace(day=dia_real)

    # Usar max(inicio_ciclo - 2 ciclos, 90 días atrás) como período
    inicio_analisis = min(
        inicio_ciclo - timedelta(days=60),
        hoy - timedelta(days=90)
    )

    tasa_ahorro = _calcular_tasa_ahorro_sync(db, usuario_id, inicio_analisis)
    score_impulsividad = _calcular_score_impulsividad_sync(db, usuario_id, inicio_analisis)
    ratio_cuotas = _calcular_ratio_cuotas_sync(db, usuario_id, inicio_analisis)
    cumplimiento_presupuesto = _calcular_cumplimiento_presupuesto_sync(db, usuario_id, inicio_analisis)
    consistencia_registro = _calcular_consistencia_registro_sync(db, usuario_id, inicio_analisis)
    porcentaje_suscripciones = _calcular_porcentaje_suscripciones_sync(db, usuario_id, inicio_analisis)

    perfil = db.execute(
        select(PerfilFinanciero).where(PerfilFinanciero.usuario_id == usuario_id)
    ).scalar_one_or_none()

    ahora = datetime.now(timezone.utc)

    if perfil:
        perfil.tasa_ahorro = tasa_ahorro
        perfil.score_impulsividad = score_impulsividad
        perfil.ratio_cuotas = ratio_cuotas
        perfil.cumplimiento_presupuesto = cumplimiento_presupuesto
        perfil.consistencia_registro = consistencia_registro
        perfil.porcentaje_suscripciones = porcentaje_suscripciones
        perfil.ultima_actualizacion = ahora
    else:
        perfil = PerfilFinanciero(
            usuario_id=usuario_id,
            tasa_ahorro=tasa_ahorro,
            score_impulsividad=score_impulsividad,
            ratio_cuotas=ratio_cuotas,
            cumplimiento_presupuesto=cumplimiento_presupuesto,
            consistencia_registro=consistencia_registro,
            porcentaje_suscripciones=porcentaje_suscripciones,
            ultima_actualizacion=ahora
        )
        db.add(perfil)

    db.commit()
    db.refresh(perfil)
    return perfil


def _obtener_perfil_sync(db, usuario_id: UUID) -> PerfilFinanciero | None:
    perfil = db.execute(
        select(PerfilFinanciero).where(PerfilFinanciero.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if not perfil:
        perfil = _calcular_y_persistir_perfil_sync(db, usuario_id)

    return perfil


# --- INTERFACES ASÍNCRONAS PÚBLICAS REQUERIDAS ---

async def calcular_tasa_ahorro(db, usuario_id: UUID, fecha_inicio: date | None = None) -> Decimal | None:
    if fecha_inicio is None:
        fecha_inicio = date.today() - timedelta(days=90)
    return _calcular_tasa_ahorro_sync(db, usuario_id, fecha_inicio)


async def calcular_score_impulsividad(db, usuario_id: UUID, fecha_inicio: date | None = None) -> int | None:
    if fecha_inicio is None:
        fecha_inicio = date.today() - timedelta(days=90)
    return _calcular_score_impulsividad_sync(db, usuario_id, fecha_inicio)


async def calcular_ratio_cuotas(db, usuario_id: UUID, fecha_inicio: date | None = None) -> Decimal | None:
    if fecha_inicio is None:
        fecha_inicio = date.today() - timedelta(days=90)
    return _calcular_ratio_cuotas_sync(db, usuario_id, fecha_inicio)


async def calcular_cumplimiento_presupuesto(db, usuario_id: UUID, fecha_inicio: date | None = None) -> Decimal | None:
    if fecha_inicio is None:
        fecha_inicio = date.today() - timedelta(days=90)
    return _calcular_cumplimiento_presupuesto_sync(db, usuario_id, fecha_inicio)


async def calcular_consistencia_registro(db, usuario_id: UUID, fecha_inicio: date | None = None) -> Decimal:
    if fecha_inicio is None:
        fecha_inicio = date.today() - timedelta(days=30)
    return _calcular_consistencia_registro_sync(db, usuario_id, fecha_inicio)


async def calcular_porcentaje_suscripciones(db, usuario_id: UUID, fecha_inicio: date | None = None) -> Decimal | None:
    if fecha_inicio is None:
        fecha_inicio = date.today() - timedelta(days=90)
    return _calcular_porcentaje_suscripciones_sync(db, usuario_id, fecha_inicio)


async def calcular_y_persistir_perfil(db, usuario_id: UUID) -> PerfilFinanciero:
    return _calcular_y_persistir_perfil_sync(db, usuario_id)


async def obtener_perfil(db, usuario_id: UUID) -> PerfilFinanciero | None:
    return _obtener_perfil_sync(db, usuario_id)


def generar_texto_contexto_ia(perfil: PerfilFinanciero) -> str:
    """Genera texto de perfil para el contexto IA, omitiendo campos NULL"""
    lineas = []
    
    campos = {
        "tasa_ahorro": ("Tasa de ahorro", lambda v: f"{float(v)*100:.1f}%"),
        "score_impulsividad": ("Impulsividad", lambda v: f"{v}/100"),
        "ratio_cuotas": ("Carga de cuotas", lambda v: f"{float(v)*100:.1f}% del ingreso"),
        "cumplimiento_presupuesto": ("Cumplimiento presupuestos", lambda v: f"{float(v)*100:.1f}%"),
        "consistencia_registro": ("Consistencia de registro", lambda v: f"{float(v)*100:.1f}% de días"),
        "porcentaje_suscripciones": ("Suscripciones", lambda v: f"{float(v)*100:.1f}% del gasto"),
    }
    
    for campo, (label, formatter) in campos.items():
        valor = getattr(perfil, campo, None)
        if valor is not None:
            lineas.append(f"- {label}: {formatter(valor)}")
    
    if not lineas:
        return ""
    
    return "PERFIL FINANCIERO DEL USUARIO:\n" + "\n".join(lineas)


def guardar_snapshot_historial(
    db: Session, 
    usuario_id: UUID, 
    periodo_inicio: date, 
    periodo_fin: date
) -> HistorialPerfilFinanciero | None:
    """
    Toma el perfil actual del usuario y lo guarda como snapshot histórico.
    Solo guarda si no existe ya un snapshot para el mismo periodo_inicio.
    """
    # Verificar si ya existe snapshot para este período
    existente = db.execute(
        select(HistorialPerfilFinanciero).where(
            HistorialPerfilFinanciero.usuario_id == usuario_id,
            HistorialPerfilFinanciero.periodo_inicio == periodo_inicio
        )
    ).scalar_one_or_none()
    
    if existente:
        return existente
    
    # Obtener perfil actual
    perfil = db.execute(
        select(PerfilFinanciero).where(
            PerfilFinanciero.usuario_id == usuario_id
        )
    ).scalar_one_or_none()
    
    if not perfil:
        return None
    
    snapshot = HistorialPerfilFinanciero(
        usuario_id=usuario_id,
        periodo_inicio=periodo_inicio,
        periodo_fin=periodo_fin,
        tasa_ahorro=perfil.tasa_ahorro,
        score_impulsividad=perfil.score_impulsividad,
        ratio_cuotas=perfil.ratio_cuotas,
        cumplimiento_presupuesto=perfil.cumplimiento_presupuesto,
        consistencia_registro=perfil.consistencia_registro,
        porcentaje_suscripciones=perfil.porcentaje_suscripciones,
        fecha_snapshot=datetime.now(timezone.utc)
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def recalcular_perfil_tras_confirmacion(db: Session, usuario_id: UUID) -> None:
    """
    Trigger síncrono para recalcular el perfil cuando se confirma una transacción.
    Se llama desde el endpoint de confirmación (síncrono).
    Falla silenciosamente para no interrumpir el flujo principal.
    """
    try:
        _calcular_y_persistir_perfil_sync(db, usuario_id)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"No se pudo recalcular perfil tras confirmación para {usuario_id}: {e}")

