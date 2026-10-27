"""
Servicio oficial y puerta única para gestionar el saldo de las billeteras en Argentum.
Ubicación: app/services/saldo_billetera_service.py

Regla de historia, foto y punto de partida (D-H3):
- El saldo inicial o foto representa el punto de partida del estado de una billetera.
- Fecha de la foto = la mayor entre la fecha del saldo (si la billetera tiene punto de partida)
  y la fecha del último control (fecha_ultimo_control); si no hay ninguna, no hay foto.
- Un control de saldo también le otorga foto a una billetera que no tenía punto de partida.
- Un movimiento con la misma fecha que la foto la mueve (afecta saldo_actual).
- Lo anterior a la fecha de la foto va a saldo_inicial; lo demás, a saldo_actual.
- Fuera de este archivo, ningún módulo de app/ escribe saldo_actual, saldo_inicial,
  fecha_saldo ni fecha_ultimo_control. Todo cambio pasa por esta puerta con bloqueo de fila.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.models.billetera import Billetera
from app.models.transaccion import MetodoPago
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


def bloquear_billeteras(
    db: Session,
    ids: Iterable[Any],
    usuario_id: Optional[UUID | str] = None,
) -> dict[UUID, Billetera]:
    """
    Bloquea las filas de las billeteras indicadas mediante SELECT ... FOR UPDATE ordenadas por id.
    Ignora valores None y repetidos. Realiza db.flush() antes de consultar con populate_existing=True.
    Si se proporciona usuario_id, filtra por dicho usuario.
    Retorna un diccionario {id: Billetera} con las billeteras encontradas.
    """
    uuid_set = set()
    for item in ids:
        if item is not None:
            try:
                uuid_set.add(item if isinstance(item, UUID) else UUID(str(item)))
            except (ValueError, TypeError):
                # Se ignora: si un identificador no tiene formato UUID válido, no puede buscarse en la tabla.
                continue

    if not uuid_set:
        return {}

    db.flush()

    ordered_ids = sorted(list(uuid_set))
    stmt = (
        select(Billetera)
        .where(Billetera.id.in_(ordered_ids))
        .order_by(Billetera.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if usuario_id is not None:
        u_uuid = usuario_id if isinstance(usuario_id, UUID) else UUID(str(usuario_id))
        stmt = stmt.where(Billetera.usuario_id == u_uuid)

    filas = db.execute(stmt).scalars().all()
    return {b.id: b for b in filas}


def fecha_creacion_billetera(billetera: Billetera) -> date:
    """
    Retorna la fecha efectiva del saldo de la billetera:
    fecha_saldo si existe; si no, la fecha de creación en hora Argentina como date.
    """
    if getattr(billetera, "fecha_saldo", None) is not None:
        return billetera.fecha_saldo

    fc = billetera.fecha_creacion
    if fc.tzinfo is None:
        fc = fc.replace(tzinfo=timezone.utc)
    return fc.astimezone(TZ_ARGENTINA).date()


def fecha_punto_partida(billetera: Billetera) -> date | None:
    """
    Retorna la fecha del punto de partida (foto) de la billetera:
    la mayor entre fecha_creacion_billetera(billetera) (solo si tiene_punto_partida)
    y billetera.fecha_ultimo_control; None si no hay ninguna.
    """
    f_creacion = fecha_creacion_billetera(billetera) if getattr(billetera, "tiene_punto_partida", True) else None
    f_control = getattr(billetera, "fecha_ultimo_control", None)

    if f_creacion is not None and f_control is not None:
        return max(f_creacion, f_control)
    if f_creacion is not None:
        return f_creacion
    if f_control is not None:
        return f_control
    return None


def movimiento_anterior_al_saldo(
    billetera: Billetera | None,
    fecha: date | datetime,
    metodo_pago: MetodoPago | str | None = None,
) -> bool:
    """
    Indica si un movimiento es anterior a la fecha efectiva de saldo de la billetera
    y no corresponde a un método de pago de crédito.
    Si la billetera no tiene punto de partida, devuelve False.
    """
    if billetera is None:
        return False

    if metodo_pago is not None:
        val = metodo_pago.value if hasattr(metodo_pago, "value") else str(metodo_pago)
        if val.lower() == "credito":
            return False

    f_pp = fecha_punto_partida(billetera)
    if f_pp is None:
        return False

    f_date = fecha.date() if isinstance(fecha, datetime) else fecha
    return f_date < f_pp


def aplicar_impacto_saldo(
    billetera: Billetera,
    impacto: Decimal | float | int | str,
    fecha: date | datetime,
) -> None:
    """
    Aplica el impacto en el saldo de la billetera según su fecha respecto al punto de partida.
    impacto es lo que se suma a saldo_actual (+monto si es ingreso o entra, -monto si es egreso o sale).
    Acepta date o datetime (un datetime se pasa a fecha de Argentina; sin zona se toma como UTC).
    Si la billetera está en una sesión (object_session), antes llama a bloquear_billeteras.
    """
    sess = object_session(billetera)
    if sess is not None:
        bloquear_billeteras(sess, [billetera.id])

    if not isinstance(impacto, Decimal):
        impacto = Decimal(str(impacto))

    if isinstance(fecha, datetime):
        if fecha.tzinfo is None:
            fecha_dt = fecha.replace(tzinfo=timezone.utc)
        else:
            fecha_dt = fecha
        fecha_date = fecha_dt.astimezone(TZ_ARGENTINA).date()
    else:
        fecha_date = fecha

    f_pp = fecha_punto_partida(billetera)

    if f_pp is None or fecha_date >= f_pp:
        if billetera.saldo_actual is None:
            billetera.saldo_actual = Decimal("0.00")
        billetera.saldo_actual += impacto
    else:
        if billetera.saldo_inicial is None:
            billetera.saldo_inicial = Decimal("0.00")
        billetera.saldo_inicial -= impacto


def registrar_control_en_saldo(
    billetera: Billetera,
    saldo_declarado: Decimal | float | int | str,
    fecha: date,
) -> Decimal:
    """
    Registra un control en el saldo de la billetera:
    bloquea la fila; devuelve monto = declarado − saldo_actual;
    saldo_actual = declarado; fecha_ultimo_control = max(anterior, fecha).
    """
    sess = object_session(billetera)
    if sess is not None:
        bloquear_billeteras(sess, [billetera.id])

    saldo_declarado_dec = Decimal(str(saldo_declarado))
    saldo_actual = billetera.saldo_actual if billetera.saldo_actual is not None else Decimal("0.00")
    monto_diferencia = saldo_declarado_dec - saldo_actual

    billetera.saldo_actual = saldo_declarado_dec

    f_ant = getattr(billetera, "fecha_ultimo_control", None)
    if f_ant is not None:
        billetera.fecha_ultimo_control = max(f_ant, fecha)
    else:
        billetera.fecha_ultimo_control = fecha

    return monto_diferencia


def revertir_control_en_saldo(
    db: Session,
    billetera: Billetera,
    ajuste: Any,
) -> None:
    """
    Revierte un control de saldo de la billetera:
    bloquea; saldo_actual −= ajuste.monto;
    fecha_ultimo_control = max(fecha) de los otros ajustes de esa billetera o None.
    """
    bloquear_billeteras(db, [billetera.id])

    monto_ajuste = Decimal(str(ajuste.monto))
    billetera.saldo_actual -= monto_ajuste

    from app.models.ajuste_saldo import AjusteSaldo
    otros_max = db.execute(
        select(func.max(AjusteSaldo.fecha)).where(
            AjusteSaldo.billetera_id == billetera.id,
            AjusteSaldo.id != ajuste.id,
        )
    ).scalar()

    billetera.fecha_ultimo_control = otros_max


def aplicar_rendimiento_en_saldo(
    billetera: Billetera,
    monto: Decimal | float | int | str,
    fecha_dt: date | datetime,
) -> None:
    """
    Aplica un rendimiento en el saldo de la billetera.
    Pasa la fecha a Argentina; si es mayor que hoy, lanza HTTPException 400.
    Si no, aplica el impacto llamando a aplicar_impacto_saldo.
    """
    if isinstance(fecha_dt, datetime):
        if fecha_dt.tzinfo is None:
            f_dt = fecha_dt.replace(tzinfo=timezone.utc)
        else:
            f_dt = fecha_dt
        f_arg = f_dt.astimezone(TZ_ARGENTINA).date()
    else:
        f_arg = fecha_dt

    hoy = hoy_argentina()
    if f_arg > hoy:
        raise HTTPException(
            status_code=400,
            detail="La fecha del rendimiento no puede ser futura.",
        )

    aplicar_impacto_saldo(billetera, Decimal(str(monto)), f_arg)


def mover_fecha_saldo(
    billetera: Billetera,
    fecha_nueva: date,
    delta: Decimal | float | int | str,
) -> None:
    """
    Aplica el cambio de fecha de saldo hacia atrás sobre la billetera:
    bloquea; saldo_actual += delta; saldo_inicial += delta; fecha_saldo = fecha_nueva.
    """
    sess = object_session(billetera)
    if sess is not None:
        bloquear_billeteras(sess, [billetera.id])

    delta_dec = Decimal(str(delta))
    if billetera.saldo_actual is None:
        billetera.saldo_actual = Decimal("0.00")
    if billetera.saldo_inicial is None:
        billetera.saldo_inicial = Decimal("0.00")

    billetera.saldo_actual += delta_dec
    billetera.saldo_inicial += delta_dec
    billetera.fecha_saldo = fecha_nueva
