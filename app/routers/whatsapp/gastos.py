"""
Consulta determinística de gastos por período y concepto para WhatsApp.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import structlog
from sqlalchemy.orm import Session

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.usuario import Usuario, Moneda
from app.routers.whatsapp.parsers import _fmt
from app.services import gastos_consulta_service, whatsapp_service
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina

logger = structlog.get_logger("whatsapp")

ETIQUETAS_PERIODO = {
    "hoy": "Hoy", "ayer": "Ayer", "semana": "Esta semana", "semana_pasada": "La semana pasada",
    "mes": "Este mes", "mes_pasado": "El mes pasado", "ciclo": "En este ciclo",
}
MSG_FALLA_GASTOS = "No pude consultar tus gastos en este momento. Probá de nuevo en unos minutos."

_TABLA_SIN_TILDES = str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuaeiouu")
_RE_TRIGGER = re.compile(r"\bcuanto\b.*\b(?:gaste|gastado|gastaste|gastamos|se me fue|se me fueron)\b")
_RE_CONCEPTO = re.compile(r"\b(?:gaste|gastado|gastaste|gastamos|fue|fueron)\b\s+(?:en|de|por|con)\s+(.+)$")
_RE_PERIODO_FRASE = re.compile(
    r"\b(?:(?:esta|la|el|este|en el|en la|durante el|durante la)\s+)?(?:semana|mes|ciclo)(?:\s+(?:pasada|pasado|anterior))?\b|\b(?:hoy|ayer)\b"
)
_CONCEPTOS_GENERICOS = {"total", "totales", "todo", "todos", "plata", "dinero", "guita", "general", "mas"}
_CONCEPTOS_NO_SOPORTADOS = {"tarjeta", "tarjetas", "credito", "tarjeta de credito"}


def _plano(texto: str) -> str:
    return texto.translate(_TABLA_SIN_TILDES).lower()


def _extraer_periodo_gastos(mensaje: str) -> str:
    p = _plano(mensaje)
    if re.search(r"\b(?:semana pasada|semana anterior)\b", p):
        return "semana_pasada"
    if re.search(r"\b(?:mes pasado|mes anterior)\b", p):
        return "mes_pasado"
    if re.search(r"\bhoy\b", p):
        return "hoy"
    if re.search(r"\bayer\b", p):
        return "ayer"
    if re.search(r"\bsemana\b", p):
        return "semana"
    if re.search(r"\bmes\b", p):
        return "mes"
    return "ciclo"


def _limpiar_periodo(original: str) -> str:
    plano = _plano(original)
    if len(plano) != len(original):
        return plano
    salida = original
    for m in reversed(list(_RE_PERIODO_FRASE.finditer(plano))):
        salida = salida[:m.start()] + " " + salida[m.end():]
    return salida


def _extraer_concepto_gastos(mensaje: str) -> str | None:
    limpio = _limpiar_periodo(mensaje)
    plano = _plano(limpio)
    m = _RE_CONCEPTO.search(plano)
    if not m:
        return None
    concepto = limpio[m.start(1):m.end(1)] if len(plano) == len(limpio) else m.group(1)
    concepto = concepto.strip()
    concepto = re.sub(r"^(?:el|la|los|las|un|una|unos|unas|mis|mi)\s+", "", concepto, flags=re.IGNORECASE)
    concepto = re.sub(r"[¿?¡!.,;:]+", " ", concepto)
    concepto = re.sub(r"\s+", " ", concepto).strip()
    concepto = re.sub(r"\s+(?:en|de|por|durante)$", "", concepto, flags=re.IGNORECASE)
    if not concepto or _plano(concepto) in _CONCEPTOS_GENERICOS:
        return None
    return concepto


def _es_consulta_gastos(mensaje: str) -> bool:
    p = _plano(mensaje)
    if re.search(r"\d", p) or not _RE_TRIGGER.search(p):
        return False
    concepto = _extraer_concepto_gastos(mensaje)
    if concepto and _plano(concepto) in _CONCEPTOS_NO_SOPORTADOS:
        return False
    return True


def _rango_periodo(clave: str, hoy: date, ciclo: tuple[date, date] | None = None) -> tuple[date, date]:
    if clave == "hoy":
        return hoy, hoy
    if clave == "ayer":
        a = hoy - timedelta(days=1)
        return a, a
    if clave == "semana":
        return hoy - timedelta(days=hoy.weekday()), hoy
    if clave == "semana_pasada":
        ini = hoy - timedelta(days=hoy.weekday() + 7)
        return ini, ini + timedelta(days=6)
    if clave == "mes":
        return hoy.replace(day=1), hoy
    if clave == "mes_pasado":
        fin = hoy.replace(day=1) - timedelta(days=1)
        return fin.replace(day=1), fin
    if ciclo is None:
        raise ValueError("El período 'ciclo' requiere las fechas del ciclo")
    return ciclo


def _formatear_respuesta_gastos(etiqueta, concepto_txt, por_descripcion, ars_total, ars_cant, usd_total, usd_cant, top) -> str:
    partes = []
    if ars_cant > 0:
        partes.append(f"{_fmt(ars_total)} ({ars_cant} {'movimiento' if ars_cant == 1 else 'movimientos'})")
    if usd_cant > 0:
        partes.append(f"{_fmt(usd_total, Moneda.USD)} ({usd_cant} {'movimiento' if usd_cant == 1 else 'movimientos'})")
    if not partes:
        if concepto_txt and por_descripcion:
            return f"{etiqueta} no encontré gastos que mencionen {concepto_txt}."
        if concepto_txt:
            return f"{etiqueta} no registraste gastos en {concepto_txt}."
        return f"{etiqueta} no registraste gastos."
    sufijo = f" en {concepto_txt}" if concepto_txt else ""
    msg = f"{etiqueta} gastaste {' y '.join(partes)}{sufijo}."
    if not concepto_txt and ars_cant > 0 and len(top) >= 2:
        msg += " Lo que más pesó: " + " | ".join(f"{nombre} {_fmt(monto)}" for nombre, monto in top) + "."
    return msg


def manejar_consulta_gastos(mensaje_texto: str, usuario: Usuario, db: Session, from_number: str, wamid: str | None = None, conv_activa=None) -> bool:
    if conv_activa:
        return False
    if not _es_consulta_gastos(mensaje_texto):
        return False
    clave = _extraer_periodo_gastos(mensaje_texto)
    concepto = _extraer_concepto_gastos(mensaje_texto)
    try:
        hoy = hoy_argentina()
        ciclo = get_ciclo_fechas(usuario, hoy) if clave == "ciclo" else None
        desde, hasta = _rango_periodo(clave, hoy, ciclo)
        categoria_id = subcategoria_id = texto_desc = concepto_txt = None
        por_descripcion = False
        if concepto:
            cat = gastos_consulta_service.resolver_concepto_catalogo(db, concepto)
            if cat:
                categoria_id, subcategoria_id, concepto_txt = cat["categoria_id"], cat["subcategoria_id"], cat["nombre"]
            else:
                texto_desc, concepto_txt, por_descripcion = concepto, f"«{concepto}»", True
        res = gastos_consulta_service.calcular_gastos_periodo(
            db, usuario.id, desde, hasta,
            categoria_id=categoria_id, subcategoria_id=subcategoria_id, texto_descripcion=texto_desc,
            top_n=0 if concepto else 3,
        )
        msg = _formatear_respuesta_gastos(
            ETIQUETAS_PERIODO[clave], concepto_txt, por_descripcion,
            float(res["ars"]["total"]), res["ars"]["cantidad"], float(res["usd"]["total"]), res["usd"]["cantidad"],
            res["top_categorias_ars"],
        )
    except Exception:
        logger.exception("Error al consultar gastos para WhatsApp")
        db.rollback()
        msg = MSG_FALLA_GASTOS
    conv = ConversacionWpp(
        usuario_id=usuario.id, wamid=wamid, mensaje_usuario=mensaje_texto, tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None, mensaje_bot=msg, intent_detectado="consultar_gastos",
        entidades={"periodo": clave, "concepto": concepto}, accion_ejecutada=None,
        confianza=Decimal("1.000"), slot_filling_activo=False, slot_filling_estado=None,
    )
    db.add(conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg)
    return True
