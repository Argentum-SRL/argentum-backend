"""
Servicio de perfil financiero de Argentum.

Administra el cálculo, persistencia y consulta del perfil financiero del usuario.
"""
from __future__ import annotations

import logging
import calendar as cal
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from uuid import UUID
from sqlalchemy import select, func, or_
from sqlalchemy.orm import joinedload, Session

from app.models.perfil_financiero import PerfilFinanciero
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.models.usuario import Usuario, Moneda
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion

from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)


# --- HELPERS PARA HISTORIAL MÍNIMO ---

def _obtener_primera_fecha_sync(db: Session, usuario_id: UUID, moneda: Moneda | None = None) -> date | None:
    query = select(func.min(Transaccion.fecha)).where(
        Transaccion.usuario_id == usuario_id,
        or_(
            Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
            Transaccion.estado_verificacion.is_(None)
        ),
        Transaccion.es_padre_cuotas == False
    )
    if moneda:
        query = query.where(Transaccion.moneda == moneda)
    res = db.execute(query).scalar()
    if res is None:
        return None
    return res.date() if isinstance(res, datetime) else res


def _validar_historial_minimo(db: Session, usuario_id: UUID, moneda: Moneda | None = None) -> bool:
    primera_fecha = _obtener_primera_fecha_sync(db, usuario_id, moneda)
    if primera_fecha is None:
        return False
    hoy = hoy_argentina()
    return (hoy - primera_fecha).days >= 90


# --- IMPLEMENTACIONES SÍNCRONAS INTERNAS ---

def _calcular_tasa_ahorro_sync_moneda(db: Session, usuario_id: UUID, fecha_inicio: date, moneda: Moneda) -> Decimal | None:
    hoy = hoy_argentina()

    txs = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.moneda == moneda,
            Transaccion.movimiento_meta_id.is_(None),
            or_(
                Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
                Transaccion.estado_verificacion.is_(None)
            ),
            Transaccion.es_padre_cuotas == False,
            Transaccion.pago_resumen_vencimiento.is_(None),
            Transaccion.fecha >= fecha_inicio,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    total_ingresos = Decimal("0")
    total_gastos = Decimal("0")
    tiene_ingreso = False

    for tx in txs:
        if tx.tipo == TipoTransaccion.INGRESO:
            total_ingresos += tx.monto
            tiene_ingreso = True
        elif tx.tipo == TipoTransaccion.EGRESO:
            total_gastos += tx.monto

    # Restricción: tasa_ahorro requiere al menos 1 ingreso en el período
    if not tiene_ingreso or total_ingresos <= 0:
        return None

    return (total_ingresos - total_gastos) / total_ingresos



def _calcular_ratio_cuotas_sync_moneda(db: Session, usuario_id: UUID, fecha_inicio: date, moneda: Moneda) -> Decimal | None:
    hoy = hoy_argentina()
    usuario = db.get(Usuario, usuario_id)
    if usuario:
        inicio_ciclo, fin_ciclo = get_ciclo_fechas(usuario, hoy)
    else:
        inicio_ciclo = date(hoy.year, hoy.month, 1)
        fin_ciclo = date(hoy.year, hoy.month, cal.monthrange(hoy.year, hoy.month)[1])

    # Cuotas no pagadas que vencen en el ciclo actual y corresponden al grupo de la moneda dada
    cuotas = db.execute(
        select(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .options(joinedload(Cuota.grupo))
        .filter(
            GrupoCuotas.usuario_id == usuario_id,
            GrupoCuotas.moneda == moneda,
            Cuota.pagada == False,
            Cuota.fecha_vencimiento >= inicio_ciclo,
            Cuota.fecha_vencimiento <= fin_ciclo
        )
    ).scalars().all()

    suma_cuotas = Decimal("0")
    for c in cuotas:
        monto = c.monto_real if c.monto_real is not None else c.monto_proyectado or Decimal("0")
        suma_cuotas += monto

    # Calcular promedios mensuales desde fecha_inicio para la moneda dada
    txs = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.moneda == moneda,
            Transaccion.movimiento_meta_id.is_(None),
            or_(
                Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
                Transaccion.estado_verificacion.is_(None)
            ),
            Transaccion.es_padre_cuotas == False,
            Transaccion.pago_resumen_vencimiento.is_(None),
            Transaccion.fecha >= fecha_inicio,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    total_ingresos = Decimal("0")
    total_gastos = Decimal("0")

    for tx in txs:
        if tx.tipo == TipoTransaccion.INGRESO:
            total_ingresos += tx.monto
        elif tx.tipo == TipoTransaccion.EGRESO:
            total_gastos += tx.monto

    cant_meses = Decimal(str(max(1.0, (hoy - fecha_inicio).days / 30.0)))
    ingreso_promedio_mensual = total_ingresos / cant_meses
    gasto_promedio_mensual = total_gastos / cant_meses

    denominador = ingreso_promedio_mensual
    if denominador == 0:
        denominador = gasto_promedio_mensual

    if denominador <= 0:
        return None

    return suma_cuotas / denominador



def _calcular_consistencia_registro_sync(
    db: Session, usuario_id: UUID, fecha_inicio: date, primera_fecha: date | datetime | None = None
) -> Decimal | None:
    hoy = hoy_argentina()

    if primera_fecha is None:
        primera_fecha = _obtener_primera_fecha_sync(db, usuario_id, None)

    if primera_fecha is None:
        return None

    primera_fecha_date = primera_fecha.date() if isinstance(primera_fecha, datetime) else primera_fecha

    dias = (hoy - fecha_inicio).days
    dias = max(1, dias)

    inicio_periodo = fecha_inicio

    dias_reales = (hoy - primera_fecha_date).days + 1
    dias_evaluados = min(dias, dias_reales)
    dias_evaluados = max(1, dias_evaluados)

    fechas_unicas = db.execute(
        select(func.distinct(Transaccion.fecha))
        .where(
            Transaccion.usuario_id == usuario_id,
            or_(
                Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
                Transaccion.estado_verificacion.is_(None)
            ),
            Transaccion.es_padre_cuotas == False,
            Transaccion.fecha >= inicio_periodo,
            Transaccion.fecha <= hoy
        )
    ).scalars().all()

    dias_con_transacciones = len(fechas_unicas)
    if dias_con_transacciones == 0:
        return None

    consistencia = Decimal(str(dias_con_transacciones)) / Decimal(str(dias_evaluados))
    return min(Decimal("1.0"), consistencia)




def _calcular_y_persistir_perfil_sync(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    from app.services.analisis_financiero_service import calcular_perfil_nuevo

    usuario = db.get(Usuario, usuario_id)
    if not usuario:
        raise ValueError(f"Usuario {usuario_id} no encontrado")

    nuevo = calcular_perfil_nuevo(db, usuario)
    perfil = db.execute(
        select(PerfilFinanciero).where(PerfilFinanciero.usuario_id == usuario_id)
    ).scalar_one_or_none()
    if perfil is None:
        perfil = PerfilFinanciero(usuario_id=usuario_id)
        db.add(perfil)
    perfil.tasa_ahorro_ars = nuevo["capacidad_ahorro"]
    perfil.tasa_ahorro_usd = None
    perfil.score_impulsividad_ars = None  # Eliminado: constructo conductual no validable
    perfil.score_impulsividad_usd = None
    perfil.ratio_cuotas_ars = nuevo["gasto_comprometido_ratio"]
    perfil.ratio_cuotas_usd = None
    perfil.cumplimiento_presupuesto = None  # Eliminado: métrica de uso de la app
    perfil.consistencia_registro = nuevo["cobertura_registro"]
    perfil.porcentaje_suscripciones_ars = None  # Integrado en gasto comprometido
    perfil.porcentaje_suscripciones_usd = None
    perfil.ultima_actualizacion = datetime.now(timezone.utc)
    db.commit()
    db.refresh(perfil)
    return perfil


def _obtener_perfil_sync(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    perfil = db.execute(
        select(PerfilFinanciero).where(PerfilFinanciero.usuario_id == usuario_id)
    ).scalar_one_or_none()

    if perfil:
        return perfil

    perfil = _calcular_y_persistir_perfil_sync(db, usuario_id)
    return perfil


# --- INTERFACES ASÍNCRONAS DE COMPATIBILIDAD ---

async def calcular_tasa_ahorro(db: Session, usuario_id: UUID, fecha_inicio: date | None = None) -> dict[str, Decimal | None]:
    if fecha_inicio is None:
        fecha_inicio = hoy_argentina() - timedelta(days=90)
    res = {"ars": None, "usd": None}
    if _validar_historial_minimo(db, usuario_id, Moneda.ARS):
        res["ars"] = _calcular_tasa_ahorro_sync_moneda(db, usuario_id, fecha_inicio, Moneda.ARS)
    if _validar_historial_minimo(db, usuario_id, Moneda.USD):
        res["usd"] = _calcular_tasa_ahorro_sync_moneda(db, usuario_id, fecha_inicio, Moneda.USD)
    return res


async def calcular_ratio_cuotas(db: Session, usuario_id: UUID, fecha_inicio: date | None = None) -> dict[str, Decimal | None]:
    if fecha_inicio is None:
        fecha_inicio = hoy_argentina() - timedelta(days=90)
    res = {"ars": None, "usd": None}
    if _validar_historial_minimo(db, usuario_id, Moneda.ARS):
        res["ars"] = _calcular_ratio_cuotas_sync_moneda(db, usuario_id, fecha_inicio, Moneda.ARS)
    if _validar_historial_minimo(db, usuario_id, Moneda.USD):
        res["usd"] = _calcular_ratio_cuotas_sync_moneda(db, usuario_id, fecha_inicio, Moneda.USD)
    return res


async def calcular_consistencia_registro(db: Session, usuario_id: UUID, fecha_inicio: date | None = None) -> Decimal | None:
    if not _validar_historial_minimo(db, usuario_id, None):
        return None
    if fecha_inicio is None:
        fecha_inicio = hoy_argentina() - timedelta(days=30)
    return _calcular_consistencia_registro_sync(db, usuario_id, fecha_inicio)


def calcular_y_persistir_perfil(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    res = _calcular_y_persistir_perfil_sync(db, usuario_id)
    if res is None:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail="Usuario no encontrado."
            )
        from uuid import uuid4
        return PerfilFinanciero(
            id=uuid4(),
            usuario_id=usuario_id,
            tasa_ahorro_ars=None,
            tasa_ahorro_usd=None,
            score_impulsividad_ars=None,
            score_impulsividad_usd=None,
            ratio_cuotas_ars=None,
            ratio_cuotas_usd=None,
            cumplimiento_presupuesto=None,
            consistencia_registro=None,
            porcentaje_suscripciones_ars=None,
            porcentaje_suscripciones_usd=None,
            ultima_actualizacion=None,
            fecha_creacion=datetime.now(timezone.utc)
        )
    return res


def obtener_perfil(db: Session, usuario_id: UUID) -> PerfilFinanciero | None:
    res = _obtener_perfil_sync(db, usuario_id)
    if res is None:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail="Usuario no encontrado."
            )
        from uuid import uuid4
        return PerfilFinanciero(
            id=uuid4(),
            usuario_id=usuario_id,
            tasa_ahorro_ars=None,
            tasa_ahorro_usd=None,
            score_impulsividad_ars=None,
            score_impulsividad_usd=None,
            ratio_cuotas_ars=None,
            ratio_cuotas_usd=None,
            cumplimiento_presupuesto=None,
            consistencia_registro=None,
            porcentaje_suscripciones_ars=None,
            porcentaje_suscripciones_usd=None,
            ultima_actualizacion=None,
            fecha_creacion=datetime.now(timezone.utc)
        )
    return res


def generar_texto_contexto_ia(perfil: PerfilFinanciero) -> str:
    """Genera texto de perfil para el contexto IA con métricas financieras objetivas y sin juicios de conducta."""
    lineas = []
    
    # Capacidad de ahorro
    if perfil.tasa_ahorro_ars is not None:
        lineas.append(f"- Capacidad de ahorro típica ARS: {float(perfil.tasa_ahorro_ars)*100:.1f}%")
        
    # Gasto comprometido sobre ingreso
    if perfil.ratio_cuotas_ars is not None:
        lineas.append(f"- Gasto comprometido sobre ingreso ARS: {float(perfil.ratio_cuotas_ars)*100:.1f}%")
        
    # Cobertura de registro
    if perfil.consistencia_registro is not None:
        lineas.append(f"- Cobertura de registro activo: {float(perfil.consistencia_registro)*100:.1f}%")
        
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
        tasa_ahorro_ars=perfil.tasa_ahorro_ars,
        tasa_ahorro_usd=perfil.tasa_ahorro_usd,
        score_impulsividad_ars=perfil.score_impulsividad_ars,
        score_impulsividad_usd=perfil.score_impulsividad_usd,
        ratio_cuotas_ars=perfil.ratio_cuotas_ars,
        ratio_cuotas_usd=perfil.ratio_cuotas_usd,
        cumplimiento_presupuesto=perfil.cumplimiento_presupuesto,
        consistencia_registro=perfil.consistencia_registro,
        porcentaje_suscripciones_ars=perfil.porcentaje_suscripciones_ars,
        porcentaje_suscripciones_usd=perfil.porcentaje_suscripciones_usd,
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


