"""
Servicio de perfil financiero de Argentum.

Administra el cálculo, persistencia y consulta del perfil financiero del usuario.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.perfil_financiero import PerfilFinanciero
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.models.usuario import Usuario

logger = logging.getLogger(__name__)


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
    perfil.ratio_cuotas_ars = nuevo["gasto_comprometido_ratio"]
    perfil.ratio_cuotas_usd = None
    perfil.consistencia_registro = nuevo["cobertura_registro"]
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
            ratio_cuotas_ars=None,
            ratio_cuotas_usd=None,
            consistencia_registro=None,
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
            ratio_cuotas_ars=None,
            ratio_cuotas_usd=None,
            consistencia_registro=None,
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

    if perfil.tasa_ahorro_ars is None and perfil.ratio_cuotas_ars is None:
        lineas.append("- Ingresos: no sabemos cuánto cobra el usuario. Debe cargar sus cobros para habilitar métricas de ahorro y compromisos sobre ingreso.")
        
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
        ratio_cuotas_ars=perfil.ratio_cuotas_ars,
        ratio_cuotas_usd=perfil.ratio_cuotas_usd,
        consistencia_registro=perfil.consistencia_registro,
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


