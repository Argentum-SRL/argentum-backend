# -*- coding: utf-8 -*-
"""
Conversión y búsqueda determinística de montos en palabras y números para WhatsApp.
Soporta modismos argentinos (lucas, palos) y números en palabras.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.routers.whatsapp.parsers import _parsear_monto_argentino

UNIDADES = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3,
    "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
}
ESPECIALES_10_19 = {
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19,
}
VEINTI = {
    "veinte": 20, "veintiuno": 21, "veintiun": 21, "veintiuna": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}
DECENAS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90,
}
CENTENAS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "doscientas": 200,
    "trescientos": 300, "trescientas": 300, "cuatrocientos": 400,
    "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700,
    "setecientas": 700, "ochocientos": 800, "ochocientas": 800,
    "novecientos": 900, "novecientas": 900,
}

TODAS_PALABRAS = (
    set(UNIDADES)
    | set(ESPECIALES_10_19)
    | set(VEINTI)
    | set(DECENAS)
    | set(CENTENAS)
    | {
        "mil", "luca", "lucas", "millon", "millones",
        "palo", "palos", "medio", "media", "y",
    }
)


def _parse_999(palabras: list[str]) -> int | None:
    if not palabras:
        return 0
    total = 0
    i = 0
    n = len(palabras)
    if i < n and palabras[i] in CENTENAS:
        total += CENTENAS[palabras[i]]
        i += 1
    if i < n:
        w = palabras[i]
        if w in DECENAS:
            total += DECENAS[w]
            i += 1
            if i < n and palabras[i] == "y":
                i += 1
                if i < n and palabras[i] in UNIDADES:
                    total += UNIDADES[palabras[i]]
                    i += 1
                else:
                    return None
        elif w in VEINTI:
            total += VEINTI[w]
            i += 1
        elif w in ESPECIALES_10_19:
            total += ESPECIALES_10_19[w]
            i += 1
        elif w in UNIDADES:
            total += UNIDADES[w]
            i += 1
        else:
            return None
    if i != n:
        return None
    return total


def monto_en_palabras(texto: str) -> Decimal | None:
    """Convierte un monto expresado enteramente en palabras a Decimal."""
    if not texto:
        return None
    t = texto.strip().lower()
    if "gamba" in t:
        return None
    if t in ("medio palo",):
        return Decimal("500000")
    if t in ("palo y medio", "un palo y medio"):
        return Decimal("1500000")
    if t in ("luca y media", "una luca y media"):
        return Decimal("1500")
    if t in ("millon y medio", "un millon y medio"):
        return Decimal("1500000")

    palabras = t.split()
    if not palabras:
        return None
    for p in palabras:
        if p not in TODAS_PALABRAS:
            return None

    # Millones o palos
    palo_idx = -1
    for idx, p in enumerate(palabras):
        if p in ("millon", "millones", "palo", "palos"):
            palo_idx = idx
            break

    if palo_idx != -1:
        antes_palo = palabras[:palo_idx]
        desp_palo = palabras[palo_idx + 1:]
        val_palo = _parse_999(antes_palo) if antes_palo else 1
        if val_palo is None or val_palo == 0:
            return None
        res = val_palo * 1000000
        if desp_palo:
            if desp_palo == ["y", "medio"] or desp_palo == ["y", "media"]:
                res += 500000
            else:
                sub = monto_en_palabras(" ".join(desp_palo))
                if sub is None:
                    return None
                res += int(sub)
        return Decimal(str(res))

    # Miles o lucas
    mil_idx = -1
    for idx, p in enumerate(palabras):
        if p in ("mil", "luca", "lucas"):
            mil_idx = idx
            break

    if mil_idx != -1:
        antes_mil = palabras[:mil_idx]
        desp_mil = palabras[mil_idx + 1:]
        val_mil = _parse_999(antes_mil) if antes_mil else 1
        if val_mil is None:
            return None
        res = val_mil * 1000
        if desp_mil:
            if desp_mil == ["y", "medio"] or desp_mil == ["y", "media"]:
                res += 500
            else:
                val_desp = _parse_999(desp_mil)
                if val_desp is None:
                    return None
                res += val_desp
        return Decimal(str(res))

    val = _parse_999(palabras)
    if val is not None and val > 0:
        return Decimal(str(val))
    return None


def buscar_monto(texto: str) -> tuple[Decimal | None, str]:
    """
    Busca el primer monto en el texto (números o palabras) y retorna (monto, texto_sin_monto).
    """
    if not texto:
        return None, ""

    # 1. Buscar monto numérico
    pat_num = re.search(
        r"(?:\$|\b)\d+(?:[.,]\d+)*(?:\s*(?:mil|k|lucas?|palos?))?\b",
        texto,
        re.IGNORECASE,
    )
    if pat_num:
        cand = pat_num.group(0).strip()
        val = _parsear_monto_argentino(cand)
        if val is not None and val > 0:
            if val % 1 == 0:
                val = Decimal(int(val))
            sin_monto = texto[:pat_num.start()] + " " + texto[pat_num.end():]
            return val, " ".join(sin_monto.split())

    # 2. Si no hay número, buscar palabras
    palabras = texto.split()
    n = len(palabras)
    mejor_val = None
    mejor_span = None

    for i in range(n):
        for j in range(n, i, -1):
            sub_words = palabras[i:j]
            sub_str = " ".join(sub_words)
            val = monto_en_palabras(sub_str)
            if val is not None and val > 0:
                mejor_val = val
                mejor_span = (i, j)
                break
        if mejor_val is not None:
            break

    if mejor_val is not None:
        i, j = mejor_span
        sin_monto = " ".join(palabras[:i] + palabras[j:])
        return mejor_val, sin_monto

    return None, texto.strip()
