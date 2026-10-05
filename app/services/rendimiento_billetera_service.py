from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.entidades import entidad_de_billetera, opciones_de_entidad
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.schemas.billetera import RendimientoEstimadoResponse
from app.services.conciliacion_service import movimientos_por_dia
from app.services.tasas_service import (
    rendimiento_por_saldos,
    saldos_diarios,
    tasa_efectiva,
    ultimas_tasas,
)
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)


def calcular_rendimiento_estimado(
    db: Session, usuario_id: UUID, billetera_id: UUID | str
) -> RendimientoEstimadoResponse:
    """
    Calcula el rendimiento estimado devengado al vuelo con saldos diarios y tasas efectivas.
    Ancla: fecha_ultimo_rendimiento (en hora de Argentina) si no es nula; si es nula,
    la mayor entre la fecha del último ajuste de saldo y la de fecha_creacion.
    El resultado nunca se persiste en saldo hasta que el usuario lo confirma explícitamente.
    """
    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    hoy = hoy_argentina()

    # 1. Determinación de fecha ancla
    if billetera.fecha_ultimo_rendimiento is not None:
        dt_rend = billetera.fecha_ultimo_rendimiento
        if dt_rend.tzinfo is None:
            dt_rend = dt_rend.replace(tzinfo=timezone.utc)
        ancla = dt_rend.astimezone(TZ_ARGENTINA).date()
    else:
        dt_creacion = billetera.fecha_creacion
        if dt_creacion.tzinfo is None:
            dt_creacion = dt_creacion.replace(tzinfo=timezone.utc)
        fecha_creacion_date = dt_creacion.astimezone(TZ_ARGENTINA).date()

        ultimo_ajuste_fecha = db.execute(
            select(func.max(AjusteSaldo.fecha)).where(AjusteSaldo.billetera_id == billetera.id)
        ).scalar_one_or_none()

        if ultimo_ajuste_fecha is not None:
            ancla = max(fecha_creacion_date, ultimo_ajuste_fecha)
        else:
            ancla = fecha_creacion_date

    dias_transcurridos = max(0, (hoy - ancla).days)

    # 2. Obtención de tasas relevantes
    ent_id = entidad_de_billetera(billetera)
    tipo_fuente, clave_base, claves_validas = opciones_de_entidad(ent_id)
    tasas_por_clave = ultimas_tasas(db, claves_validas) if claves_validas else {}

    # 3. Tasa efectiva
    tasa_ef = tasa_efectiva(billetera, tasas_por_clave, billetera.saldo_actual, hoy)

    # 4. Tasa automática vigente (independientemente de si manda la manual)
    tna_automatica: Decimal | None = None
    if not billetera.es_efectivo and tipo_fuente:
        clave_auto = None
        if tipo_fuente == "cuenta":
            if billetera.nivel_tasa and billetera.nivel_tasa in claves_validas:
                clave_auto = billetera.nivel_tasa
            else:
                clave_auto = clave_base
        elif tipo_fuente == "fci":
            clave_auto = clave_base

        if clave_auto and clave_auto in tasas_por_clave:
            fila_auto = tasas_por_clave[clave_auto]
            if (hoy - fila_auto.fecha_dato).days <= 7:
                tna_automatica = fila_auto.tna


    tiene_tna = (tasa_ef.tna is not None and not tasa_ef.vieja)

    # 5. Rendimiento estimado por saldos diarios
    if not tiene_tna:
        rendimiento_estimado = None
    else:
        movs = movimientos_por_dia(db, billetera.id, desde=ancla, hasta=hoy)
        netos_por_dia: dict[date, Decimal] = {}
        for d_mov, vals in movs.items():
            netos_por_dia[d_mov] = vals["entradas"] - vals["salidas"]

        ajustes_rows = db.execute(
            select(AjusteSaldo.fecha, AjusteSaldo.monto).where(
                AjusteSaldo.billetera_id == billetera.id,
                AjusteSaldo.fecha >= ancla,
                AjusteSaldo.fecha <= hoy,
            )
        ).all()
        for f_aj, m_aj in ajustes_rows:
            netos_por_dia[f_aj] = netos_por_dia.get(f_aj, Decimal("0.00")) + m_aj

        saldos = saldos_diarios(billetera.saldo_actual, netos_por_dia, ancla, hoy)
        rendimiento_estimado = rendimiento_por_saldos(saldos, tasa_ef.tna, tasa_ef.tope)

    return RendimientoEstimadoResponse(
        billetera_id=billetera.id,
        tiene_tna=tiene_tna,
        tna=tasa_ef.tna if tiene_tna else None,
        saldo_actual=billetera.saldo_actual,
        dias_transcurridos=dias_transcurridos,
        fecha_ultimo_rendimiento=billetera.fecha_ultimo_rendimiento,
        rendimiento_estimado=rendimiento_estimado,
        origen_tasa=tasa_ef.origen,
        fecha_dato_tasa=tasa_ef.fecha_dato,
        tasa_vieja=tasa_ef.vieja,
        tope=tasa_ef.tope,
        entidad_id=tasa_ef.entidad_id,
        clave_tasa=tasa_ef.clave,
        tna_automatica=tna_automatica,
    )


def confirmar_rendimiento(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID | str,
    monto: Decimal,
    fecha: datetime | None = None,
    commit: bool = True,  # commit=False: la operación de afuera hace el único commit
) -> Billetera:
    """
    Confirma un rendimiento manual:
    - Crea la fila en rendimientos_billetera.
    - Suma el monto a billetera.saldo_actual.
    - Actualiza fecha_ultimo_rendimiento a ahora.
    - NO toca la tabla transacciones.
    """
    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    if billetera.estado != EstadoBilletera.ACTIVA:
        raise HTTPException(
            status_code=400,
            detail="No se pueden registrar rendimientos en una billetera archivada."
        )

    if monto <= Decimal("0"):
        raise HTTPException(
            status_code=400,
            detail="El monto del rendimiento debe ser mayor a 0."
        )

    ahora_dt = datetime.now(timezone.utc)
    fecha_operacion = fecha if fecha is not None else ahora_dt

    rendimiento = RendimientoBilletera(
        billetera_id=billetera.id,
        monto=monto,
        fecha=fecha_operacion,
        fecha_creacion=ahora_dt,
    )
    db.add(rendimiento)

    billetera.saldo_actual += monto
    billetera.fecha_ultimo_rendimiento = ahora_dt

    if commit:
        db.commit()
        db.refresh(billetera)
    else:
        db.flush()
    return billetera
