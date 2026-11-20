# -*- coding: utf-8 -*-
"""
Cálculo del último uso real por usuario (web y WhatsApp) y estado de cuenta para el panel de admin.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models.conversacion_wpp import ConversacionWpp
from app.models.evento_uso import EventoUso
from app.models.transaccion import OrigenTransaccion, Transaccion
from app.models.usuario import EstadoUsuario, Usuario
from app.services import costos_service, uso_service

# Los eventos terminados en _wpp se registran desde WhatsApp; no son uso de la web.
EVENTOS_WHATSAPP: tuple[str, ...] = tuple(
    e for e in uso_service.EVENTOS if e.endswith("_wpp")
)

# 7 y 30 días son la semana y el mes de las herramientas de análisis (activos de la semana y del mes);
# el mes es el ciclo natural de una app de finanzas.
DIAS_NUEVO = 7
DIAS_ACTIVO = 7
DIAS_POCO_USO = 30
ESTADOS_VISIBLES: tuple[str, ...] = (
    "activo",
    "nuevo",
    "poco_uso",
    "en_desuso",
    "sin_empezar",
    "suspendida",
    "sin_verificar",
)
ORIGENES_CARGA: tuple[OrigenTransaccion, ...] = (
    OrigenTransaccion.MANUAL,
    OrigenTransaccion.IA_WPP,
    OrigenTransaccion.IA_PDF,
)


def _normalizar_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _resolver_uso(
    ultimo_acceso: datetime | None,
    max_evento_web: datetime | None,
    max_conv_wpp: datetime | None,
) -> dict:
    acc = _normalizar_utc(ultimo_acceso)
    ev_web = _normalizar_utc(max_evento_web)
    wpp = _normalizar_utc(max_conv_wpp)

    if acc is not None and ev_web is not None:
        web = acc if acc >= ev_web else ev_web
    else:
        web = acc if acc is not None else ev_web

    if web is not None and wpp is not None:
        if wpp >= web:
            ultimo = wpp
            canal = "whatsapp"
        else:
            ultimo = web
            canal = "web"
    elif wpp is not None:
        ultimo = wpp
        canal = "whatsapp"
    elif web is not None:
        ultimo = web
        canal = "web"
    else:
        ultimo = None
        canal = None

    return {
        "ultimo_uso": ultimo,
        "ultimo_uso_canal": canal,
        "ultimo_uso_web": web,
        "ultimo_uso_whatsapp": wpp,
    }


def ultimos_usos(db: Session, usuario_ids: list[UUID]) -> dict[UUID, dict]:
    """
    Devuelve el último uso web, WhatsApp, combinado y canal para cada usuario solicitado
    usando consultas agrupadas (sin N+1).
    """
    if not usuario_ids:
        return {}

    accesos_rows = db.execute(
        select(Usuario.id, Usuario.ultimo_acceso).where(Usuario.id.in_(usuario_ids))
    ).all()
    accesos_map: dict[UUID, datetime | None] = {uid: acc for uid, acc in accesos_rows}

    web_rows = db.execute(
        select(EventoUso.usuario_id, func.max(EventoUso.fecha))
        .where(
            EventoUso.usuario_id.in_(usuario_ids),
            ~EventoUso.evento.in_(EVENTOS_WHATSAPP),
        )
        .group_by(EventoUso.usuario_id)
    ).all()
    web_map: dict[UUID, datetime | None] = {
        uid: f for uid, f in web_rows if uid is not None
    }

    wpp_rows = db.execute(
        select(ConversacionWpp.usuario_id, func.max(ConversacionWpp.fecha))
        .where(ConversacionWpp.usuario_id.in_(usuario_ids))
        .group_by(ConversacionWpp.usuario_id)
    ).all()
    wpp_map: dict[UUID, datetime | None] = {uid: f for uid, f in wpp_rows}

    return {
        uid: _resolver_uso(
            accesos_map.get(uid),
            web_map.get(uid),
            wpp_map.get(uid),
        )
        for uid in usuario_ids
    }


def expresiones_ultimo_uso():
    """
    Devuelve (subq_web, subq_wpp, expr_ultimo_uso) armada con case()
    para compatibilidad con SQLite y PostgreSQL.
    """
    subq_web = (
        select(
            EventoUso.usuario_id.label("usuario_id"),
            func.max(EventoUso.fecha).label("max_web"),
        )
        .where(
            EventoUso.usuario_id.is_not(None),
            ~EventoUso.evento.in_(EVENTOS_WHATSAPP),
        )
        .group_by(EventoUso.usuario_id)
        .subquery()
    )
    subq_wpp = (
        select(
            ConversacionWpp.usuario_id.label("usuario_id"),
            func.max(ConversacionWpp.fecha).label("max_wpp"),
        )
        .group_by(ConversacionWpp.usuario_id)
        .subquery()
    )

    acc = Usuario.ultimo_acceso
    ev_web = subq_web.c.max_web
    wpp = subq_wpp.c.max_wpp

    web_expr = case(
        (acc.is_(None), ev_web),
        (ev_web.is_(None), acc),
        (acc >= ev_web, acc),
        else_=ev_web,
    )
    ultimo_expr = case(
        (web_expr.is_(None), wpp),
        (wpp.is_(None), web_expr),
        (wpp >= web_expr, wpp),
        else_=web_expr,
    )
    return subq_web, subq_wpp, ultimo_expr


def estado_cuenta(usuario: Usuario) -> str:
    """Traduce EstadoUsuario al estado visible de cuenta."""
    if usuario.estado == EstadoUsuario.INACTIVO:
        return "suspendida"
    if usuario.estado == EstadoUsuario.PENDIENTE_VERIFICACION:
        return "sin_verificar"
    return "activa"


def estado_visible(
    usuario: Usuario,
    ultimo_uso: datetime | None,
    ahora: datetime,
) -> str:
    """
    Calcula el estado de uso visible para administración según orden estricto de prioridad.
    """
    if usuario.estado == EstadoUsuario.INACTIVO:
        return "suspendida"
    if usuario.estado == EstadoUsuario.PENDIENTE_VERIFICACION:
        return "sin_verificar"
    if not usuario.onboarding_completo:
        return "sin_empezar"

    ahora_utc = _normalizar_utc(ahora) or datetime.now(timezone.utc)
    registro_utc = _normalizar_utc(usuario.fecha_registro)
    if registro_utc is not None and registro_utc >= ahora_utc - timedelta(days=DIAS_NUEVO):
        return "nuevo"

    ultimo_utc = _normalizar_utc(ultimo_uso)
    if ultimo_utc is not None and ultimo_utc >= ahora_utc - timedelta(days=DIAS_ACTIVO):
        return "activo"
    if ultimo_utc is not None and ultimo_utc >= ahora_utc - timedelta(days=DIAS_POCO_USO):
        return "poco_uso"
    return "en_desuso"


def expresion_estado_visible(ahora: datetime):
    """
    Devuelve (subq_web, subq_wpp, expr) con la misma regla de estado_visible en SQL usando case()
    sobre las subconsultas de expresiones_ultimo_uso. Tiene que dar lo mismo que estado_visible.
    """
    ahora_utc = _normalizar_utc(ahora) or datetime.now(timezone.utc)
    limite_nuevo = ahora_utc - timedelta(days=DIAS_NUEVO)
    limite_activo = ahora_utc - timedelta(days=DIAS_ACTIVO)
    limite_poco_uso = ahora_utc - timedelta(days=DIAS_POCO_USO)

    subq_web, subq_wpp, ultimo_expr = expresiones_ultimo_uso()
    expr = case(
        (Usuario.estado == EstadoUsuario.INACTIVO, "suspendida"),
        (Usuario.estado == EstadoUsuario.PENDIENTE_VERIFICACION, "sin_verificar"),
        (Usuario.onboarding_completo == False, "sin_empezar"),
        (Usuario.fecha_registro >= limite_nuevo, "nuevo"),
        (and_(ultimo_expr.is_not(None), ultimo_expr >= limite_activo), "activo"),
        (and_(ultimo_expr.is_not(None), ultimo_expr >= limite_poco_uso), "poco_uso"),
        else_="en_desuso",
    )
    return subq_web, subq_wpp, expr


def movimientos_mes(
    db: Session,
    usuario_ids: list[UUID],
    hoy: date,
) -> dict[UUID, int]:
    """
    Cuenta los movimientos del mes calendario (Argentina) por usuario.
    Cuenta lo que la persona cargó (web, WhatsApp o PDF); no cuenta suscripciones automáticas,
    cuotas generadas ni espejos de reintegros.
    """
    if not usuario_ids:
        return {}
    inicio = costos_service.inicio_mes_ar(hoy)
    rows = db.execute(
        select(Transaccion.usuario_id, func.count(Transaccion.id))
        .where(
            Transaccion.usuario_id.in_(usuario_ids),
            Transaccion.fecha_creacion >= inicio,
            Transaccion.origen.in_(ORIGENES_CARGA),
            Transaccion.es_cuota_hija == False,
            Transaccion.tipo_espejo.is_(None),
        )
        .group_by(Transaccion.usuario_id)
    ).all()
    conteos = {uid: int(cnt or 0) for uid, cnt in rows}
    return {uid: conteos.get(uid, 0) for uid in usuario_ids}


def expresion_movimientos_mes(hoy: date):
    """
    Devuelve (subq, expr) con la misma regla de movimientos_mes para ordenar en SQL.
    """
    inicio = costos_service.inicio_mes_ar(hoy)
    subq = (
        select(
            Transaccion.usuario_id.label("usuario_id"),
            func.count(Transaccion.id).label("cantidad"),
        )
        .where(
            Transaccion.fecha_creacion >= inicio,
            Transaccion.origen.in_(ORIGENES_CARGA),
            Transaccion.es_cuota_hija == False,
            Transaccion.tipo_espejo.is_(None),
        )
        .group_by(Transaccion.usuario_id)
        .subquery()
    )
    expr = func.coalesce(subq.c.cantidad, 0)
    return subq, expr
