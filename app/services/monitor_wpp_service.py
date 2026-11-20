"""
Servicio para el Monitor de WhatsApp y Costos de IA del panel de administración.
Calcula métricas de calidad de intents, desglose de costos, topes diarios y salud operativa.
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.conversacion_wpp import ConversacionWpp
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.models.notificacion import Notificacion
from app.models.rate_limit import RateLimit
from app.models.uso_ia import UsoIA
from app.models.usuario import Usuario
from app.services import (
    admin_registro_service,
    cola_wpp_service,
    costos_service,
    openai_costos_service,
    salientes_wpp_service,
)
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


INTENTS_CORRECCION = {"deshacer", "confirmar_deshacer", "corregir", "confirmar_corregir"}
FUNCIONES_PERMITIDAS = (
    "texto_whatsapp",
    "audio_whatsapp",
    "imagen_whatsapp",
    "pdf_whatsapp",
    "importacion_pdf",
)


def obtener_ventana_desde(dias: int) -> datetime:
    """Calcula el inicio de la ventana temporal (00:00 ART) según los días especificados."""
    hoy = hoy_argentina()
    dia_inicio = hoy - timedelta(days=dias - 1)
    return datetime(dia_inicio.year, dia_inicio.month, dia_inicio.day, 0, 0, 0, tzinfo=TZ_ARGENTINA)


def ventana_de_rango(desde: date, hasta: date) -> tuple[datetime, datetime]:
    """
    Devuelve (inicio, fin) en hora de Argentina para un rango inclusive:
    inicio = desde 00:00 AR; fin = hasta + 1 día 00:00 AR (fin exclusivo).
    """
    inicio = datetime(desde.year, desde.month, desde.day, 0, 0, 0, tzinfo=TZ_ARGENTINA)
    dia_siguiente = hasta + timedelta(days=1)
    fin = datetime(
        dia_siguiente.year,
        dia_siguiente.month,
        dia_siguiente.day,
        0,
        0,
        0,
        tzinfo=TZ_ARGENTINA,
    )
    return inicio, fin


def _categorizar_conversacion(
    conv: ConversacionWpp,
    sig_conv: ConversacionWpp | None,
) -> str:
    """
    Categoriza una conversación según el orden de precedencia estricto:
    1. tope: intent_detectado == "tope_superado"
    2. error_ia: error_ia es True
    3. no_entendido: intent_detectado == "desconocido"
    4. correccion: intent en INTENTS_CORRECCION
    5. deshecho_o_corregido: el siguiente mensaje del usuario es "correccion" y llegó dentro de los 10 minutos
    6. entendido_primera: todo lo demás
    """
    if conv.intent_detectado == "tope_superado":
        return "tope"
    if conv.error_ia:
        return "error_ia"
    if conv.intent_detectado == "desconocido":
        return "no_entendido"
    if conv.intent_detectado in INTENTS_CORRECCION:
        return "correccion"
    if sig_conv is not None:
        delta = (sig_conv.fecha - conv.fecha).total_seconds()
        if 0 <= delta <= 600:
            if not sig_conv.error_ia and sig_conv.intent_detectado in INTENTS_CORRECCION:
                return "deshecho_o_corregido"
    return "entendido_primera"


def _obtener_conversaciones_con_siguiente(
    db: Session,
    desde: datetime,
    hasta_fin: datetime | None = None,
) -> list[tuple[ConversacionWpp, str]]:
    """
    Obtiene todas las conversaciones de la ventana y calcula su categoría,
    buscando el siguiente mensaje de cada usuario sin límite de ventana.
    """
    conds = [ConversacionWpp.fecha >= desde]
    if hasta_fin is not None:
        conds.append(ConversacionWpp.fecha < hasta_fin)

    # 1. Traer todas las conversaciones de la ventana ordenadas por usuario y fecha ascendente
    convs_ventana = db.scalars(
        select(ConversacionWpp)
        .where(*conds)
        .order_by(ConversacionWpp.usuario_id, ConversacionWpp.fecha.asc())
    ).all()

    if not convs_ventana:
        return []

    # Agrupar por usuario
    convs_por_usuario: dict[UUID, list[ConversacionWpp]] = {}
    for c in convs_ventana:
        convs_por_usuario.setdefault(c.usuario_id, []).append(c)

    resultado: list[tuple[ConversacionWpp, str]] = []

    for uid, u_convs in convs_por_usuario.items():
        n = len(u_convs)
        for i, c in enumerate(u_convs):
            if i + 1 < n:
                sig = u_convs[i + 1]
            else:
                # Buscar el siguiente mensaje en la base sin límite de ventana
                sig = db.scalars(
                    select(ConversacionWpp)
                    .where(
                        ConversacionWpp.usuario_id == uid,
                        ConversacionWpp.fecha > c.fecha,
                    )
                    .order_by(ConversacionWpp.fecha.asc())
                    .limit(1)
                ).first()

            cat = _categorizar_conversacion(c, sig)
            resultado.append((c, cat))

    return resultado


def calcular_percentil(valores: list[int], p: float) -> int | float:
    """
    Calcula percentil continuo con interpolación lineal (equivalente a percentile_cont).
    """
    n = len(valores)
    if n == 0:
        return 0
    if n == 1:
        return valores[0]
    idx = p * (n - 1)
    i = int(idx)
    f = idx - i
    if i >= n - 1:
        return valores[-1]
    val = valores[i] + f * (valores[i + 1] - valores[i])
    return int(val) if val.is_integer() else round(val, 2)


def obtener_resumen_monitor(
    db: Session,
    dias: int | None = None,
    hoy: date | None = None,
    desde: date | None = None,
    hasta: date | None = None,
    ahora: datetime | None = None,
) -> dict[str, Any]:
    """
    Genera el resumen integral para el monitor de WhatsApp y costos de IA.
    """
    hoy_ref = hoy if hoy is not None else hoy_argentina()
    ahora_utc = ahora if ahora is not None else datetime.now(timezone.utc)
    if ahora_utc.tzinfo is None:
        ahora_utc = ahora_utc.replace(tzinfo=timezone.utc)

    if desde is not None and hasta is not None:
        ventana_inicio, ventana_fin = ventana_de_rango(desde, hasta)
        fecha_desde_costos = desde
        fecha_hasta_costos = hasta
        dias_salientes = max(1, (hoy_ref - desde).days + 1)
    else:
        dias_eff = dias if dias is not None else 1
        ventana_inicio = obtener_ventana_desde(dias_eff)
        ventana_fin = None
        fecha_desde_costos = hoy_ref - timedelta(days=dias_eff - 1)
        fecha_hasta_costos = hoy_ref
        dias_salientes = dias_eff

    # 1. Calidad de entendimiento
    convs_con_cat = _obtener_conversaciones_con_siguiente(db, ventana_inicio, ventana_fin)
    conteo_cat: dict[str, int] = {
        "entendido_primera": 0,
        "no_entendido": 0,
        "deshecho_o_corregido": 0,
        "correccion": 0,
        "error_ia": 0,
        "tope": 0,
    }
    for _, cat in convs_con_cat:
        if cat in conteo_cat:
            conteo_cat[cat] += 1

    total_convs = len(convs_con_cat)
    total_calidad = total_convs - conteo_cat["tope"]
    divisor_porcentaje = total_calidad - conteo_cat["correccion"]
    if divisor_porcentaje > 0:
        pct_entendido = round((conteo_cat["entendido_primera"] / divisor_porcentaje) * 100.0, 1)
    else:
        pct_entendido = 0.0

    calidad = {
        "total": total_calidad,
        "entendidos_primera": conteo_cat["entendido_primera"],
        "porcentaje_entendidos_primera": pct_entendido,
        "no_entendidos": conteo_cat["no_entendido"],
        "deshechos_o_corregidos": conteo_cat["deshecho_o_corregido"],
        "correcciones": conteo_cat["correccion"],
        "errores_ia": conteo_cat["error_ia"],
        "topes": conteo_cat["tope"],
    }

    # 2. Costo de IA (de uso_ia)
    conds_uso = [UsoIA.fecha >= ventana_inicio]
    if ventana_fin is not None:
        conds_uso.append(UsoIA.fecha < ventana_fin)
    usos = db.scalars(
        select(UsoIA)
        .where(*conds_uso)
        .order_by(UsoIA.fecha.asc())
    ).all()

    total_costo_usd = Decimal("0.000000")
    por_funcion: dict[str, Decimal] = {f: Decimal("0.000000") for f in FUNCIONES_PERMITIDAS}
    costos_por_dia: dict[str, Decimal] = {}
    costos_por_usuario: dict[UUID, Decimal] = {}
    sin_usuario_usd = Decimal("0.000000")

    for u in usos:
        costo = u.costo_usd or Decimal("0")
        total_costo_usd += costo

        if u.funcion in por_funcion:
            por_funcion[u.funcion] += costo

        dia_ar = u.fecha.astimezone(TZ_ARGENTINA).strftime("%Y-%m-%d")
        costos_por_dia[dia_ar] = costos_por_dia.get(dia_ar, Decimal("0.000000")) + costo

        if u.usuario_id is not None:
            costos_por_usuario[u.usuario_id] = costos_por_usuario.get(u.usuario_id, Decimal("0.000000")) + costo
        else:
            sin_usuario_usd += costo

    por_dia_list = [
        {"fecha": d, "total_usd": costos_por_dia[d]}
        for d in sorted(costos_por_dia.keys())
    ]

    # Top 10 usuarios por mayor costo
    top_usuarios_sorted = sorted(costos_por_usuario.items(), key=lambda x: x[1], reverse=True)[:10]
    emails_map: dict[UUID, str | None] = {}
    if top_usuarios_sorted:
        uids = [uid for uid, _ in top_usuarios_sorted]
        usuarios_db = db.execute(select(Usuario.id, Usuario.email).where(Usuario.id.in_(uids))).all()
        for u_id, u_email in usuarios_db:
            emails_map[u_id] = u_email

    por_usuario_list = [
        {"usuario_id": uid, "email": emails_map.get(uid), "total_usd": c_usd}
        for uid, c_usd in top_usuarios_sorted
    ]

    costos_mes = costos_service.costo_mes_por_usuario(db, hoy_ref)
    top_mes = sorted(
        ((uid, c) for uid, c in costos_mes.items() if c.total_ars > 0),
        key=lambda item: (item[1].total_ars, item[1].ia_usd, str(item[0])),
        reverse=True,
    )[:10]
    nombres_mes: dict[UUID, str] = {}
    if top_mes:
        uids_mes = [uid for uid, _ in top_mes]
        usuarios_mes = db.scalars(select(Usuario).where(Usuario.id.in_(uids_mes))).all()
        for u_m in usuarios_mes:
            nombres_mes[u_m.id] = admin_registro_service.nombre_visible(u_m)

    por_usuario_mes_list = [
        {
            "usuario_id": uid,
            "nombre": nombres_mes.get(uid, "Sin nombre"),
            "total_ars": c.total_ars,
            "ia_usd": c.ia_usd,
            "whatsapp_cobrados": c.whatsapp_cobrados,
        }
        for uid, c in top_mes
    ]

    openai_datos = openai_costos_service.costos_por_dia(
        fecha_desde_costos, fecha_hasta_costos, ahora_utc
    )
    openai_estado = openai_costos_service.estado_openai(openai_datos)
    if openai_datos is not None:
        valores_rango = {
            d: monto
            for d, monto in openai_datos.items()
            if fecha_desde_costos <= d <= fecha_hasta_costos
        }
        openai_usd: Decimal | None = sum(valores_rango.values(), Decimal("0"))
        openai_hasta: date | None = max(valores_rango.keys()) if valores_rango else None
    else:
        openai_usd = None
        openai_hasta = None

    costo_dict = {
        "total_usd": total_costo_usd,
        "por_funcion": por_funcion,
        "por_dia": por_dia_list,
        "por_usuario": por_usuario_list,
        "por_usuario_mes": por_usuario_mes_list,
        "sin_usuario_usd": sin_usuario_usd,
        "openai_usd": openai_usd,
        "openai_estado": openai_estado,
        "openai_hasta": openai_hasta,
    }

    # 3. Topes de hoy
    inicio_hoy_ar = datetime(hoy_ref.year, hoy_ref.month, hoy_ref.day, 0, 0, 0, tzinfo=TZ_ARGENTINA)
    gasto_hoy_usd = db.scalar(
        select(func.coalesce(func.sum(UsoIA.costo_usd), 0)).where(UsoIA.fecha >= inicio_hoy_ar)
    )
    gasto_hoy_usd = Decimal(str(gasto_hoy_usd)) if gasto_hoy_usd is not None else Decimal("0.000000")
    presupuesto_usd = Decimal(str(getattr(settings, "IA_PRESUPUESTO_DIARIO_USD", 1.0)))

    if gasto_hoy_usd >= presupuesto_usd * Decimal("2.0"):
        nivel = "sin_ia"
    elif gasto_hoy_usd >= presupuesto_usd * Decimal("1.0"):
        nivel = "sin_archivos"
    elif gasto_hoy_usd >= presupuesto_usd * Decimal("0.5"):
        nivel = "aviso"
    else:
        nivel = "normal"

    tope_msg = getattr(settings, "IA_MAX_MENSAJES_DIA_USUARIO", 150)
    tope_arch = getattr(settings, "IA_MAX_ARCHIVOS_DIA_USUARIO", 20)

    usuarios_tope_mensajes = db.scalar(
        select(func.count(RateLimit.id)).where(
            RateLimit.accion == "mensajes_ia_dia",
            RateLimit.expira_en > ahora_utc,
            RateLimit.cantidad > tope_msg,
        )
    ) or 0

    usuarios_tope_archivos = db.scalar(
        select(func.count(RateLimit.id)).where(
            RateLimit.accion == "archivos_ia_dia",
            RateLimit.expira_en > ahora_utc,
            RateLimit.cantidad > tope_arch,
        )
    ) or 0

    topes_hoy_dict = {
        "gasto_hoy_usd": gasto_hoy_usd,
        "presupuesto_usd": presupuesto_usd,
        "nivel": nivel,
        "usuarios_tope_mensajes": usuarios_tope_mensajes,
        "usuarios_tope_archivos": usuarios_tope_archivos,
    }

    # 4. Salud
    tiempos = sorted([
        c.tiempo_respuesta_ms
        for c, _ in convs_con_cat
        if c.tiempo_respuesta_ms is not None
    ])
    mensajes_con_tiempo = len(tiempos)
    if mensajes_con_tiempo > 0:
        mediana_ms = calcular_percentil(tiempos, 0.5)
        p90_ms = calcular_percentil(tiempos, 0.9)
    else:
        mediana_ms = None
        p90_ms = None

    conds_notif_env = [
        Notificacion.created_at >= ventana_inicio,
        Notificacion.canal_whatsapp == True,
        Notificacion.enviada_whatsapp == True,
    ]
    if ventana_fin is not None:
        conds_notif_env.append(Notificacion.created_at < ventana_fin)
    avisos_enviados = db.scalar(select(func.count(Notificacion.id)).where(*conds_notif_env)) or 0

    conds_notif_sin = [
        Notificacion.created_at >= ventana_inicio,
        Notificacion.canal_whatsapp == True,
        Notificacion.enviada_whatsapp == False,
    ]
    if ventana_fin is not None:
        conds_notif_sin.append(Notificacion.created_at < ventana_fin)
    avisos_sin_enviar = db.scalar(select(func.count(Notificacion.id)).where(*conds_notif_sin)) or 0

    estados_cola = (
        cola_wpp_service.FALLO,
        cola_wpp_service.INTERRUMPIDO,
        cola_wpp_service.VENCIDO,
    )
    conds_cola = [
        MensajeWhatsappProcesado.fecha_recepcion >= ventana_inicio,
        MensajeWhatsappProcesado.estado.in_(estados_cola),
    ]
    if ventana_fin is not None:
        conds_cola.append(MensajeWhatsappProcesado.fecha_recepcion < ventana_fin)
    cola_rows = db.execute(
        select(MensajeWhatsappProcesado.estado, func.count(MensajeWhatsappProcesado.id))
        .where(*conds_cola)
        .group_by(MensajeWhatsappProcesado.estado)
    ).all()
    cola_map = {est: int(cnt) for est, cnt in cola_rows}

    salud_dict = {
        "tiempo_respuesta_mediana_ms": mediana_ms,
        "tiempo_respuesta_p90_ms": p90_ms,
        "mensajes_con_tiempo": mensajes_con_tiempo,
        "avisos_whatsapp_enviados": avisos_enviados,
        "avisos_whatsapp_sin_enviar": avisos_sin_enviar,
        "wpp_fallados": cola_map.get(cola_wpp_service.FALLO, 0),
        "wpp_interrumpidos": cola_map.get(cola_wpp_service.INTERRUMPIDO, 0),
        "wpp_vencidos": cola_map.get(cola_wpp_service.VENCIDO, 0),
    }

    return {
        "calidad": calidad,
        "costo": costo_dict,
        "topes_hoy": topes_hoy_dict,
        "salud": salud_dict,
        "whatsapp_salientes": salientes_wpp_service.resumen_salientes(db, dias_salientes),
    }


def obtener_conversaciones_monitor(
    db: Session,
    dias: int | None = None,
    categoria: str | None = None,
    pagina: int = 1,
    desde: date | None = None,
    hasta: date | None = None,
) -> dict[str, Any]:
    """
    Obtiene las conversaciones de la ventana paginadas de a 50 de la más nueva a la más vieja,
    con filtro opcional por categoría y ocultando textos si el usuario no otorgó consentimiento.
    """
    if desde is not None and hasta is not None:
        ventana_inicio, ventana_fin = ventana_de_rango(desde, hasta)
    else:
        ventana_inicio = obtener_ventana_desde(dias if dias is not None else 1)
        ventana_fin = None
    convs_con_cat = _obtener_conversaciones_con_siguiente(db, ventana_inicio, ventana_fin)

    # Filtrar por categoría si se especificó y no es vacía
    if categoria and categoria.strip():
        cat_filtro = categoria.strip()
        convs_filtradas = [(c, cat) for c, cat in convs_con_cat if cat == cat_filtro]
    else:
        convs_filtradas = convs_con_cat

    # Ordenar de la más nueva a la más vieja
    convs_filtradas.sort(key=lambda x: x[0].fecha, reverse=True)

    total = len(convs_filtradas)
    limite = 50
    offset = max(0, (pagina - 1) * limite)
    slice_convs = convs_filtradas[offset : offset + limite]

    # Cargar usuarios para verificar permiso y obtener emails
    uids = list({c.usuario_id for c, _ in slice_convs})
    usuarios_db = db.execute(
        select(Usuario.id, Usuario.email, Usuario.permiso_lectura_mensajes_en).where(Usuario.id.in_(uids))
    ).all() if uids else []
    user_info = {u[0]: {"email": u[1], "permiso": u[2]} for u in usuarios_db}

    items = []
    for c, cat in slice_convs:
        u_data = user_info.get(c.usuario_id, {})
        permiso_activo = u_data.get("permiso") is not None
        tipo_msg = c.tipo_mensaje.value if hasattr(c.tipo_mensaje, "value") else str(c.tipo_mensaje)

        items.append({
            "fecha": c.fecha,
            "usuario_id": c.usuario_id,
            "email": u_data.get("email"),
            "tipo_mensaje": tipo_msg,
            "intent": c.intent_detectado,
            "categoria": cat,
            "confianza": c.confianza,
            "tiempo_respuesta_ms": c.tiempo_respuesta_ms,
            "error_ia": c.error_ia,
            "texto_visible": permiso_activo,
            "mensaje_usuario": c.mensaje_usuario if permiso_activo else None,
            "transcripcion": c.transcripcion if permiso_activo else None,
            "mensaje_bot": c.mensaje_bot if permiso_activo else None,
        })

    return {
        "total": total,
        "pagina": pagina,
        "items": items,
    }
