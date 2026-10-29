# -*- coding: utf-8 -*-
"""
Servicio para cálculo y envío del reporte semanal de uso de Argentum.
Mide activación, retención, costos y uso de herramientas sin datos personales ni montos individuales.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session, joinedload
import structlog

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.job_lock import intentar_tomar_lock_job, liberar_lock_job
from app.models.conversacion_wpp import ConversacionWpp
from app.models.evento_uso import EventoUso
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.transaccion import OrigenTransaccion, Transaccion
from app.models.uso_ia import UsoIA
from app.models.usuario import Usuario
from app.services.email_service import _enviar_email
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)
struct_logger = structlog.get_logger(__name__)


def dia_ar(dt: Optional[datetime | date]) -> Optional[date]:
    """
    Convierte un datetime a fecha local argentina (America/Argentina/Buenos_Aires).
    Si dt no tiene zona horaria se asume UTC.
    """
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_ARGENTINA).date()


def _norm_utc(dt: Optional[datetime]) -> datetime:
    """Normaliza un datetime a UTC timezone-aware para comparaciones consistentes."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _formatear_decimal(val: Decimal, decimales: int) -> str:
    """Formatea un Decimal con HALF_UP, coma decimal y sin separador de miles."""
    d = val.quantize(Decimal("10") ** -decimales, rounding=ROUND_HALF_UP)
    return f"{d:.{decimales}f}".replace(".", ",")


def _formatear_porcentaje(a: int, n: int) -> str:
    """Formatea un porcentaje redondeado HALF_UP a entero seguido de %."""
    if n == 0:
        return "0%"
    pct = (Decimal(a) * Decimal(100) / Decimal(n)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{pct}%"


def calcular_reporte(db: Session, hoy: date) -> dict[str, Any]:
    """
    Calcula las métricas de uso y costos para la semana que va desde hoy - 7 hasta hoy - 1.
    """
    semana_inicio = hoy - timedelta(days=7)
    semana_fin = hoy - timedelta(days=1)

    # Ventana amplia para consultas en UTC (+- 2 días de margen)
    dt_min_utc = datetime(hoy.year, hoy.month, hoy.day, tzinfo=timezone.utc) - timedelta(days=50)
    dt_max_utc = datetime(hoy.year, hoy.month, hoy.day, 23, 59, 59, tzinfo=timezone.utc) + timedelta(days=2)

    # 1. Cuentas totales y usuarios
    usuarios = db.query(Usuario).all()
    cuentas_total = len(usuarios)
    altas_semana = 0
    for u in usuarios:
        d_alta = dia_ar(u.fecha_registro)
        if d_alta and semana_inicio <= d_alta <= semana_fin:
            altas_semana += 1

    # 2. Días activos de cada usuario (web y WhatsApp)
    origenes_carga = (
        OrigenTransaccion.MANUAL,
        OrigenTransaccion.IA_WPP,
        OrigenTransaccion.IA_PDF,
    )

    dias_activos_web: dict[UUID, set[date]] = defaultdict(set)
    dias_activos_wpp: dict[UUID, set[date]] = defaultdict(set)

    # 2.a. Eventos visita en eventos_uso
    eventos_visita = (
        db.query(EventoUso)
        .filter(EventoUso.evento == "visita")
        .all()
    )
    for ev in eventos_visita:
        if ev.usuario_id and ev.dia:
            dias_activos_web[ev.usuario_id].add(ev.dia)

    # 2.b. Transacciones MANUAL (web) y IA_WPP / IA_PDF (WhatsApp)
    txs = (
        db.query(Transaccion)
        .options(joinedload(Transaccion.categoria), joinedload(Transaccion.subcategoria), joinedload(Transaccion.usuario))
        .filter(
            Transaccion.fecha_creacion >= dt_min_utc,
            Transaccion.fecha_creacion <= dt_max_utc,
        )
        .all()
    )

    txs_por_usuario: dict[UUID, list[Transaccion]] = defaultdict(list)
    for tx in txs:
        txs_por_usuario[tx.usuario_id].append(tx)
        d_tx = dia_ar(tx.fecha_creacion)
        if not d_tx:
            continue
        if tx.origen == OrigenTransaccion.MANUAL:
            dias_activos_web[tx.usuario_id].add(d_tx)
        elif tx.origen in (OrigenTransaccion.IA_WPP, OrigenTransaccion.IA_PDF):
            dias_activos_wpp[tx.usuario_id].add(d_tx)

    # 2.c. Conversaciones de WhatsApp
    convs = (
        db.query(ConversacionWpp)
        .filter(
            ConversacionWpp.fecha >= dt_min_utc,
            ConversacionWpp.fecha <= dt_max_utc,
        )
        .all()
    )
    for c in convs:
        d_c = dia_ar(c.fecha)
        if d_c and c.usuario_id:
            dias_activos_wpp[c.usuario_id].add(d_c)

    # 3. Usuarios activos en la semana
    activos_web_set: set[UUID] = set()
    activos_wpp_set: set[UUID] = set()
    activos_set: set[UUID] = set()

    todos_usuarios_con_actividad = set(dias_activos_web.keys()) | set(dias_activos_wpp.keys())
    for uid in todos_usuarios_con_actividad:
        web_en_semana = any(semana_inicio <= d <= semana_fin for d in dias_activos_web.get(uid, set()))
        wpp_en_semana = any(semana_inicio <= d <= semana_fin for d in dias_activos_wpp.get(uid, set()))
        if web_en_semana:
            activos_web_set.add(uid)
        if wpp_en_semana:
            activos_wpp_set.add(uid)
        if web_en_semana or wpp_en_semana:
            activos_set.add(uid)

    activos = len(activos_set)
    activos_web = len(activos_web_set)
    activos_wpp = len(activos_wpp_set)

    # 4. Activación
    # cohorte = usuarios con dia_ar(fecha_registro) entre hoy - 8 y hoy - 2
    f_act_desde = hoy - timedelta(days=8)
    f_act_hasta = hoy - timedelta(days=2)
    cohorte_activacion = [
        u for u in usuarios
        if dia_ar(u.fecha_registro) and f_act_desde <= dia_ar(u.fecha_registro) <= f_act_hasta
    ]

    activados = 0
    for u in cohorte_activacion:
        # primera transacción con origen de carga
        txs_u = [
            t for t in txs_por_usuario.get(u.id, [])
            if t.origen in origenes_carga
        ]
        if txs_u:
            txs_u.sort(key=lambda t: _norm_utc(t.fecha_creacion))
            primera_tx = txs_u[0]
            limite_activacion = _norm_utc(u.fecha_registro) + timedelta(hours=24)
            if _norm_utc(primera_tx.fecha_creacion) <= limite_activacion:
                activados += 1

    # 5. Retención a 7 días
    # cohorte = dia_ar(fecha_registro) entre hoy - 20 y hoy - 14
    f_ret7_desde = hoy - timedelta(days=20)
    f_ret7_hasta = hoy - timedelta(days=14)
    cohorte_ret7 = [
        u for u in usuarios
        if dia_ar(u.fecha_registro) and f_ret7_desde <= dia_ar(u.fecha_registro) <= f_ret7_hasta
    ]

    retenidos_7 = 0
    for u in cohorte_ret7:
        alta = dia_ar(u.fecha_registro)
        if not alta:
            continue
        ventana_desde = alta + timedelta(days=7)
        ventana_hasta = alta + timedelta(days=13)
        dias_u = dias_activos_web.get(u.id, set()) | dias_activos_wpp.get(u.id, set())
        if any(ventana_desde <= d <= ventana_hasta for d in dias_u):
            retenidos_7 += 1

    # 6. Retención a 30 días
    # cohorte entre hoy - 43 y hoy - 37
    f_ret30_desde = hoy - timedelta(days=43)
    f_ret30_hasta = hoy - timedelta(days=37)
    cohorte_ret30 = [
        u for u in usuarios
        if dia_ar(u.fecha_registro) and f_ret30_desde <= dia_ar(u.fecha_registro) <= f_ret30_hasta
    ]

    retenidos_30 = 0
    for u in cohorte_ret30:
        alta = dia_ar(u.fecha_registro)
        if not alta:
            continue
        ventana_desde = alta + timedelta(days=30)
        ventana_hasta = alta + timedelta(days=36)
        dias_u = dias_activos_web.get(u.id, set()) | dias_activos_wpp.get(u.id, set())
        if any(ventana_desde <= d <= ventana_hasta for d in dias_u):
            retenidos_30 += 1

    # 7. Costo IA y Costo WhatsApp
    dt_sem_min_utc = datetime(semana_inicio.year, semana_inicio.month, semana_inicio.day, tzinfo=timezone.utc) - timedelta(days=2)
    dt_sem_max_utc = datetime(semana_fin.year, semana_fin.month, semana_fin.day, 23, 59, 59, tzinfo=timezone.utc) + timedelta(days=2)

    usos_ia = (
        db.query(UsoIA)
        .filter(UsoIA.fecha >= dt_sem_min_utc, UsoIA.fecha <= dt_sem_max_utc)
        .all()
    )
    costo_ia = Decimal("0.00")
    for uso in usos_ia:
        d_uso = dia_ar(uso.fecha)
        if d_uso and semana_inicio <= d_uso <= semana_fin:
            costo_ia += Decimal(str(uso.costo_usd))

    mensajes_wpp = (
        db.query(MensajeSalienteWpp)
        .filter(
            MensajeSalienteWpp.fecha >= dt_sem_min_utc,
            MensajeSalienteWpp.fecha <= dt_sem_max_utc,
            MensajeSalienteWpp.enviado.is_(True),
            MensajeSalienteWpp.tipo_cobro == "regular",
        )
        .all()
    )
    cobrados_wpp = 0
    for m in mensajes_wpp:
        d_m = dia_ar(m.fecha)
        if d_m and semana_inicio <= d_m <= semana_fin:
            cobrados_wpp += 1

    costo_wpp = Decimal(cobrados_wpp) * settings.WHATSAPP_TARIFA_SERVICIO_USD

    costo_por_activo = None
    if activos > 0:
        costo_por_activo = (costo_ia + costo_wpp) / Decimal(activos)

    # 8. Funciones de la web
    eventos_semana = (
        db.query(EventoUso)
        .filter(EventoUso.dia >= semana_inicio, EventoUso.dia <= semana_fin)
        .all()
    )
    func_usuarios: dict[str, set[UUID]] = defaultdict(set)
    for ev in eventos_semana:
        if ev.usuario_id:
            func_usuarios[ev.evento].add(ev.usuario_id)

    permitirse_count = len(func_usuarios.get("permitirse", set()))
    calculadora_cuotas_count = len(func_usuarios.get("calculadora_cuotas", set()))
    descargar_datos_count = len(func_usuarios.get("descargar_datos", set()))

    # 9. WhatsApp en Otros
    # transacciones de la semana (dia_ar(fecha_creacion)) con origen IA_WPP o IA_PDF,
    # reintegro_de_id nulo y es_cuota_hija falso
    txs_wpp_semana: list[Transaccion] = []
    for tx in txs:
        d_tx = dia_ar(tx.fecha_creacion)
        if (
            d_tx
            and semana_inicio <= d_tx <= semana_fin
            and tx.origen in (OrigenTransaccion.IA_WPP, OrigenTransaccion.IA_PDF)
            and tx.reintegro_de_id is None
            and not tx.es_cuota_hija
        ):
            txs_wpp_semana.append(tx)

    total_wpp = len(txs_wpp_semana)

    en_otros_txs: list[Transaccion] = []
    for tx in txs_wpp_semana:
        cat_nom = tx.categoria.nombre if tx.categoria else ""
        subcat_nom = tx.subcategoria.nombre if tx.subcategoria else None
        if cat_nom == "Otros" or not subcat_nom or subcat_nom == "Otros":
            en_otros_txs.append(tx)

    en_otros = len(en_otros_txs)

    # descripciones: las no vacías de en_otros cuyo usuario tiene permiso_lectura_mensajes_en no nulo,
    # sin repetir, en orden de fecha_creacion, como mucho 20
    en_otros_txs.sort(key=lambda t: _norm_utc(t.fecha_creacion))
    seen_desc: set[str] = set()
    descripciones: list[str] = []
    for tx in en_otros_txs:
        u = tx.usuario
        if u and u.permiso_lectura_mensajes_en is not None:
            desc = (tx.descripcion or "").strip()
            if desc and desc not in seen_desc:
                seen_desc.add(desc)
                descripciones.append(desc)
                if len(descripciones) == 20:
                    break

    return {
        "semana_inicio": semana_inicio,
        "semana_fin": semana_fin,
        "cuentas_total": cuentas_total,
        "altas_semana": altas_semana,
        "activos": activos,
        "activos_web": activos_web,
        "activos_wpp": activos_wpp,
        "activacion": {
            "a": activados,
            "n": len(cohorte_activacion),
            "p": _formatear_porcentaje(activados, len(cohorte_activacion)),
        },
        "retencion_7": {
            "r": retenidos_7,
            "n": len(cohorte_ret7),
            "p": _formatear_porcentaje(retenidos_7, len(cohorte_ret7)),
        },
        "retencion_30": {
            "r": retenidos_30,
            "n": len(cohorte_ret30),
            "p": _formatear_porcentaje(retenidos_30, len(cohorte_ret30)),
        },
        "costo_ia": costo_ia,
        "cobrados_wpp": cobrados_wpp,
        "costo_wpp": costo_wpp,
        "costo_por_activo": costo_por_activo,
        "funciones": {
            "permitirse": permitirse_count,
            "calculadora_cuotas": calculadora_cuotas_count,
            "descargar_datos": descargar_datos_count,
        },
        "otros": {
            "total_wpp": total_wpp,
            "en_otros": en_otros,
            "descripciones": descripciones,
        },
    }


def armar_texto(rep: dict[str, Any], hoy: date) -> str:
    """
    Construye el texto plano del reporte semanal según la plantilla exacta requerida.
    """
    f_desde = hoy - timedelta(days=7)
    f_hasta = hoy - timedelta(days=1)

    lineas: list[str] = []
    lineas.append(f"Uso de Argentum del {f_desde:%d/%m} al {f_hasta:%d/%m} (sin datos personales).")
    lineas.append("")
    lineas.append(f"Cuentas: {rep['cuentas_total']} en total, {rep['altas_semana']} nuevas en la semana.")
    lineas.append(f"Activos en la semana: {rep['activos']} (web {rep['activos_web']}, WhatsApp {rep['activos_wpp']}).")

    # Activación
    act = rep["activacion"]
    if act["n"] > 0:
        lineas.append(f"Activación: {act['a']} de {act['n']} cuentas nuevas cargaron algo en sus primeras 24 horas ({act['p']}).")
    else:
        lineas.append("Activación: no hubo cuentas nuevas para medir.")

    # Retención a 7 días
    r7 = rep["retencion_7"]
    if r7["n"] > 0:
        lineas.append(f"Retención a 7 días: {r7['r']} de {r7['n']} ({r7['p']}).")
    else:
        lineas.append("Retención a 7 días: no hay cuentas para medir.")

    # Retención a 30 días
    r30 = rep["retencion_30"]
    if r30["n"] > 0:
        lineas.append(f"Retención a 30 días: {r30['r']} de {r30['n']} ({r30['p']}).")
    else:
        lineas.append("Retención a 30 días: no hay cuentas para medir.")

    lineas.append("")

    # Costo
    costo_ia_str = _formatear_decimal(rep["costo_ia"], 2)
    costo_wpp_str = _formatear_decimal(rep["costo_wpp"], 2)
    lineas.append(
        "Costo de la semana: IA USD "
        + costo_ia_str
        + f"; WhatsApp {rep['cobrados_wpp']} mensajes cobrados por Meta (≈ USD "
        + costo_wpp_str
        + ")."
    )

    if rep["costo_por_activo"] is not None:
        costo_activo_str = _formatear_decimal(rep["costo_por_activo"], 4)
        lineas.append("Costo por usuario activo: USD " + costo_activo_str + ".")
    else:
        lineas.append("Costo por usuario activo: sin usuarios activos.")

    lineas.append("")

    # Funciones
    fn = rep["funciones"]
    lineas.append(
        f"Funciones de la web (personas en la semana): ¿Me lo puedo permitir? {fn['permitirse']}; "
        f"calculadora de cuotas {fn['calculadora_cuotas']}; descargar mis datos {fn['descargar_datos']}."
    )

    lineas.append("")

    # WhatsApp en Otros
    ot = rep["otros"]
    if ot["total_wpp"] > 0:
        lineas.append(f"WhatsApp en \"Otros\": {ot['en_otros']} de {ot['total_wpp']} movimientos quedaron en Otros o sin subcategoría.")
    else:
        lineas.append("WhatsApp en \"Otros\": no hubo movimientos por WhatsApp.")

    if ot["descripciones"]:
        lineas.append("Descripciones (solo de quien dio permiso):")
        for desc in ot["descripciones"]:
            lineas.append(f"- {desc}")

    return "\n".join(lineas)


def destinatarios_reporte() -> list[str]:
    """
    Retorna la lista de destinatarios del reporte semanal de uso.
    Lee settings.REPORTE_USO_EMAILS; si está vacía, recurre a ADMIN_ALERT_EMAIL; si no, [].
    """
    raw_emails = settings.REPORTE_USO_EMAILS
    if raw_emails:
        emails = [e.strip() for e in raw_emails.split(",") if e.strip()]
        if emails:
            return emails

    admin_email = os.environ.get("ADMIN_ALERT_EMAIL", "").strip()
    if admin_email:
        return [admin_email]

    return []


def _job_reporte_uso_semanal(db: Optional[Session] = None, hoy: Optional[date] = None) -> bool:
    """
    Job semanal (lunes 09:07) que calcula y envía el reporte de uso al equipo.
    Purga eventos_uso con más de 400 días de antigüedad.
    """
    sesion_propia = False
    if db is None:
        db = SessionLocal()
        sesion_propia = True

    ref_hoy = hoy if hoy is not None else hoy_argentina()

    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_reporte_uso_semanal"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_reporte_uso_semanal",
            )
            return False
        lock_adquirido = True

        # 1. Purgar eventos_uso con dia < ref_hoy - 400
        fecha_purga = ref_hoy - timedelta(days=400)
        stmt_del = delete(EventoUso).where(EventoUso.dia < fecha_purga)
        db.execute(stmt_del)
        db.commit()

        # 2. Destinatarios
        destinatarios = destinatarios_reporte()
        if not destinatarios:
            logger.warning("No hay destinatarios configurados para el reporte semanal de uso.")
            return False

        # 3. Calcular y armar texto
        rep = calcular_reporte(db, ref_hoy)
        texto = armar_texto(rep, ref_hoy)
        f_desde = ref_hoy - timedelta(days=7)
        f_hasta = ref_hoy - timedelta(days=1)
        asunto = f"[Argentum] Uso de la semana del {f_desde:%d/%m} al {f_hasta:%d/%m}"

        salio_al_menos_uno = False
        for dest in destinatarios:
            exito = _enviar_email(dest, asunto, texto)
            if exito:
                salio_al_menos_uno = True

        return salio_al_menos_uno
    except Exception as e:
        logger.exception("Error en job _job_reporte_uso_semanal: %s", e)
        if sesion_propia:
            db.rollback()
        raise
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_reporte_uso_semanal")
        if sesion_propia:
            db.close()

