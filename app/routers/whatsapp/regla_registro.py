# -*- coding: utf-8 -*-
"""Reglas determinísticas para decidir registro directo vs confirmación en WhatsApp."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata

from app.core.config import settings
from app.models.conversacion_wpp import TipoMensajeWpp
from app.routers.whatsapp.montos_palabras import buscar_monto
from app.utils.texto import normalizar_texto

FRASES_PREGUNTA_PLATA: tuple[str, ...] = (
    "da para",
    "me da para",
    "me alcanza",
    "alcanza para",
    "me banco",
    "tengo para",
    "puedo gastar",
    "puedo comprar",
    "me puedo comprar",
    "me lo puedo permitir",
    "llego a fin de mes",
    "llego al",
)

_RE_FECHA_CORTA = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
_RE_FRASES_PREGUNTA = tuple(
    re.compile(rf"(?<!\w){re.escape(f)}(?!\w)")
    for f in FRASES_PREGUNTA_PLATA
)


def _sin_tildes_min(texto: str) -> str:
    nfd = unicodedata.normalize("NFD", str(texto))
    limpio = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", limpio.lower()).strip()


def confianza_ia_baja(valor: object) -> bool:
    """True si la IA devolvió confianza explícita menor al umbral configurado."""
    if valor is None:
        return False
    return float(valor) < settings.UMBRAL_CONFIANZA_IA


def es_mensaje_pregunta(texto: str | None) -> bool:
    """Detecta si el mensaje expresa una pregunta o duda de si alcanza la plata."""
    if not texto or not str(texto).strip():
        return False
    crudo = str(texto).strip()
    if crudo.startswith("¿") or crudo.endswith("?"):
        return True
    base = _sin_tildes_min(crudo)
    for rx in _RE_FRASES_PREGUNTA:
        if rx.search(base):
            return True
    sin_signos = re.sub(r"[^\w\s]", "", base).strip()
    if sin_signos and sin_signos.split()[-1] == "llego":
        return True
    return False


def montos_del_texto(texto: str | None) -> list[Decimal]:
    """Extrae todos los montos reconocidos por montos_palabras y jerga.py en el texto."""
    if not texto or not str(texto).strip():
        return []
    limpio = _sin_tildes_min(str(texto))
    limpio = _RE_FECHA_CORTA.sub(" ", limpio)
    encontrados: list[Decimal] = []
    restante = limpio
    for _ in range(30):
        monto, nuevo = buscar_monto(restante)
        if monto is None:
            break
        encontrados.append(monto)
        if nuevo == restante:
            break
        restante = nuevo
    return encontrados


def monto_esta_en_texto(texto: str | None, monto: object) -> bool:
    """True si el monto indicado está presente entre los montos entendidos en texto."""
    if monto is None:
        return False
    try:
        objetivo = Decimal(str(monto))
    except (InvalidOperation, ValueError, TypeError):
        return False
    return any(m == objetivo for m in montos_del_texto(texto))


def categoria_adivinada(entidades: dict | None) -> bool:
    """True si la categoría salió por descarte o es 'Otros' sin subcategoría."""
    if not isinstance(entidades, dict):
        return True
    if entidades.get("categoria_por_descarte"):
        return True
    cat_raw = str(entidades.get("categoria") or "").strip()
    sub_raw = str(entidades.get("subcategoria") or "").strip()
    if ">" in cat_raw or "›" in cat_raw:
        sep = ">" if ">" in cat_raw else "›"
        cat_part, sub_part = [p.strip() for p in cat_raw.split(sep, 1)]
    else:
        cat_part, sub_part = cat_raw, sub_raw
    if normalizar_texto(cat_part) == "otros" and not sub_part:
        return True
    return False


def cumple_regla_registro_directo(fila, entidades: dict) -> bool:
    """Valida condiciones de seguridad D-D2 (pregunta, monto en texto y categoría)."""
    msg_orig = (
        entidades.get("mensaje_original") if isinstance(entidades, dict) else None
    )
    if msg_orig:
        texto = str(msg_orig)
    else:
        es_audio = getattr(fila, "tipo_mensaje", None) == TipoMensajeWpp.AUDIO
        transcr = getattr(fila, "transcripcion", None)
        if es_audio and transcr:
            texto = str(transcr)
        else:
            texto = str(getattr(fila, "mensaje_usuario", None) or transcr or "")

    if es_mensaje_pregunta(texto):
        return False

    if categoria_adivinada(entidades):
        return False

    operaciones = entidades.get("operaciones")
    adicionales = entidades.get("transacciones_adicionales")
    es_lote = bool(
        (isinstance(adicionales, list) and len(adicionales) > 0)
        or (isinstance(operaciones, list) and len(operaciones) > 0)
    )

    if es_lote:
        items = (
            operaciones
            if isinstance(operaciones, list) and operaciones
            else [entidades] + (adicionales if isinstance(adicionales, list) else [])
        )
        for it in items:
            if not isinstance(it, dict):
                return False
            if categoria_adivinada(it):
                return False
            if not monto_esta_en_texto(texto, it.get("monto")):
                return False
        return True

    if not monto_esta_en_texto(texto, entidades.get("monto")):
        return False

    return True
