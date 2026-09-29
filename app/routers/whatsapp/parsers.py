"""
Parsers y formateadores determinísticos puros para el flujo de WhatsApp IA.
Funciones puras (sin I/O de red ni base de datos).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from app.core.catalogo_suscripciones import identificar_servicio_en_texto
from app.utils.formato import formatear_monto
from app.utils.fecha import hoy_argentina
from app.models.transaccion import Moneda
from app.utils.texto import normalizar_texto

_MESES_RIOPLATENSE = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]


def _fmt(monto: float, moneda: Moneda | str = Moneda.ARS) -> str:
    """Formatea un número con formato argentino y símbolo según moneda."""
    return formatear_monto(monto, moneda)


def _nombre_corto_categoria(nombre: str | None) -> str:
    """
    Si la categoría viene en formato 'Categoría > Subcategoría',
    devuelve solo 'Subcategoría'. Si no tiene '>', devuelve el nombre tal cual.
    """
    if not nombre:
        return "Otros"
    if ">" in nombre:
        return nombre.split(">", 1)[1].strip()
    return nombre.strip()


def _parsear_monto_texto_cuota(t: str) -> Decimal | None:
    """Parsea montos en texto soportando modismos argentinos como '80 mil', '80k', '1 palo'."""
    t = t.lower().strip()
    m_mil = re.match(r"^([0-9]+(?:[.,][0-9]+)?)\s*(?:mil|k)$", t)
    if m_mil:
        val = float(m_mil.group(1).replace(",", ".")) * 1000
        return Decimal(str(int(val)))
    m_palo = re.match(r"^([0-9]+(?:[.,][0-9]+)?)\s*(?:palos?|lucas?)$", t)
    if m_palo:
        mult = 1000000 if "palo" in t else 1000
        val = float(m_palo.group(1).replace(",", ".")) * mult
        return Decimal(str(int(val)))
    t_clean = re.sub(r"[^\d.,]", "", t)
    if not t_clean:
        return None
    if "." in t_clean and "," in t_clean:
        t_clean = t_clean.replace(".", "").replace(",", ".")
    elif "." in t_clean:
        partes = t_clean.split(".")
        if len(partes[-1]) == 3 and len(partes) > 1:
            t_clean = t_clean.replace(".", "")
    elif "," in t_clean:
        t_clean = t_clean.replace(",", ".")
    try:
        return Decimal(t_clean)
    except Exception:
        return None


def _interpretar_cuotas(
    mensaje: str,
    monto_ia: Decimal | None,
) -> tuple[int, Decimal | None, Decimal | None, bool, str | None]:
    """
    Interpreta cantidad de cuotas y determina si el monto es total o por cuota (Tarea 5).
    Retorna: (cant_cuotas, monto_cuota, monto_total, es_ambiguo, err_msg)
    """
    m_norm = normalizar_texto(mensaje)

    # Caso 1: "en X cuotas de M" o "X cuotas de M" -> M es por cuota
    pat_de = re.search(
        r"(?:en\s+)?(\d+)\s*(?:cuotas?|pagos?)\s+de\s+(?:cada\s+una\s+de\s+)?(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)(?:\b|$)",
        m_norm,
    )
    if pat_de:
        cant = int(pat_de.group(1))
        if cant < 1 or cant > 48:
            return cant, None, None, False, "La cantidad de cuotas debe ser entre 1 y 48."
        m_str = pat_de.group(2).strip()
        m_val = _parsear_monto_texto_cuota(m_str)
        if m_val is None and monto_ia is not None:
            m_val = monto_ia
        if m_val is not None:
            monto_cuota = m_val
            monto_total = Decimal(str(cant)) * monto_cuota
            return cant, monto_cuota, monto_total, False, None

    # Caso 2: "M en X cuotas" o "M a pagar en X cuotas" -> M es el total
    pat_en = re.search(
        r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\s+(?:a\s+pagar\s+)?en\s+(\d+)\s*(?:cuotas?|pagos?)(?:\b|$)",
        m_norm,
    )
    if pat_en:
        cant = int(pat_en.group(2))
        if cant < 1 or cant > 48:
            return cant, None, None, False, "La cantidad de cuotas debe ser entre 1 y 48."
        m_str = pat_en.group(1).strip()
        m_val = _parsear_monto_texto_cuota(m_str)
        if m_val is None and monto_ia is not None:
            m_val = monto_ia
        if m_val is not None:
            monto_total = m_val
            monto_cuota = round(monto_total / Decimal(str(cant)), 2)
            return cant, monto_cuota, monto_total, False, None

    # Caso 3: Menciona cuotas ("X cuotas") pero sin encajar claramente en Caso 1 ni Caso 2
    pat_gen = re.search(r"(?:en\s+)?(\d+)\s*(?:cuotas?|pagos?)", m_norm)
    if pat_gen:
        cant = int(pat_gen.group(1))
        if cant < 1 or cant > 48:
            return cant, None, None, False, "La cantidad de cuotas debe ser entre 1 y 48."
        if cant > 1 and monto_ia is not None:
            return cant, None, None, True, None

    # Caso 4: No menciona cuotas (1 pago)
    if monto_ia is not None:
        return 1, monto_ia, monto_ia, False, None

    return 1, None, None, False, None


def _resolver_y_validar_fecha(fecha_val: str | None) -> tuple[date, str | None]:
    """
    Resuelve la fecha de la transacción y valida reglas de negocio:
    - Fechas futuras: se avisa y se usa hoy.
    - Fechas de más de 60 días atrás: se avisa y se usa hoy.
    - Fechas válidas (hasta 60 días atrás y <= hoy): se usan tal cual.
    - Si no se especifica fecha o es inválida: se usa hoy sin aviso.
    Retorna (fecha_resuelta, aviso_o_none).
    """
    hoy = hoy_argentina()
    if not fecha_val:
        return hoy, None

    try:
        fecha_candidata = date.fromisoformat(str(fecha_val))
    except Exception:
        return hoy, None

    limite_antiguedad = hoy - timedelta(days=60)
    if fecha_candidata > hoy:
        return hoy, "No puedo registrar movimientos con fecha futura porque todavía no ocurrieron. Va a quedar con fecha de hoy."
    elif fecha_candidata < limite_antiguedad:
        return hoy, "No puedo registrar movimientos de más de 60 días atrás. Va a quedar con fecha de hoy."
    else:
        return fecha_candidata, None


def _formatear_fecha_natural(fecha_obj: date) -> str | None:
    """
    Formatea una fecha de forma natural en rioplatense:
    - Hoy: None (no se menciona)
    - Ayer: 'ayer'
    - Anteayer: 'anteayer'
    - Otra fecha: 'el 31 de agosto' (o 'el 31 de agosto de 2025' si difiere el año)
    """
    hoy = hoy_argentina()
    if fecha_obj == hoy:
        return None
    delta = (hoy - fecha_obj).days
    if delta == 1:
        return "ayer"
    elif delta == 2:
        return "anteayer"
    else:
        mes_nombre = _MESES_RIOPLATENSE[fecha_obj.month - 1]
        if fecha_obj.year != hoy.year:
            return f"el {fecha_obj.day} de {mes_nombre} de {fecha_obj.year}"
        return f"el {fecha_obj.day} de {mes_nombre}"


def _parsear_monto_argentino(texto: str) -> Decimal | None:
    if not texto:
        return None
    limpio = texto.strip().lower()
    limpio = limpio.replace("$", "").replace("ars", "").replace("usd", "").strip()
    multiplicador = Decimal("1")
    if limpio.endswith("k"):
        multiplicador = Decimal("1000")
        limpio = limpio[:-1].strip()
    elif "mil" in limpio.split() or limpio.endswith("mil") or re.search(r"\bmil\b", limpio):
        multiplicador = Decimal("1000")
        limpio = re.sub(r"\bmil\b", "", limpio).strip()
    elif "luca" in limpio:
        multiplicador = Decimal("1000")
        limpio = re.sub(r"lucas?", "", limpio).strip()
    elif "palo" in limpio:
        multiplicador = Decimal("1000000")
        limpio = re.sub(r"palos?", "", limpio).strip()

    if "." in limpio and "," in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif "." in limpio:
        partes = limpio.split(".")
        if len(partes) == 2 and len(partes[1]) == 3:
            limpio = partes[0] + partes[1]
        elif len(partes) > 2:
            limpio = "".join(partes)
        else:
            if len(partes[1]) == 3:
                limpio = partes[0] + partes[1]
            else:
                limpio = partes[0] + "." + partes[1]
    elif "," in limpio:
        limpio = limpio.replace(",", ".")

    try:
        val = Decimal(limpio) * multiplicador
        if val > 0:
            return val
    except Exception:
        pass
    return None


def _extraer_frecuencia_mencionada(mensaje: str) -> str | None:
    norm = normalizar_texto(mensaje)
    if not norm:
        return None
    if re.search(r"\b(?:por\s+mes|al\s+mes|cada\s+mes|todos\s+los\s+meses|mensual(?:mente)?)\b", norm):
        return "mensual"
    if re.search(r"\b(?:bimestral(?:mente)?|cada\s+2\s+meses|cada\s+dos\s+meses)\b", norm):
        return "bimestral"
    if re.search(r"\b(?:trimestral(?:mente)?|cada\s+3\s+meses|cada\s+tres\s+meses)\b", norm):
        return "trimestral"
    if re.search(r"\b(?:semestral(?:mente)?|cada\s+6\s+meses|cada\s+seis\s+meses)\b", norm):
        return "semestral"
    if re.search(r"\b(?:por\s+a[nñ]o|al\s+a[nñ]o|todos\s+los\s+a[nñ]os|anual(?:mente)?)\b", norm):
        return "anual"
    return None


def _extraer_nombre_servicio(mensaje: str) -> str | None:
    serv_cat = identificar_servicio_en_texto(mensaje)
    if serv_cat:
        return serv_cat["nombre"]

    m1 = re.search(
        r"(?:empec[eé]|empece)\s+a\s+pagar\s+(?:\$?\s*[\d\.,]+(?:k|\s*mil)?\s*(?:d[oó]lares|usd|pesos)?\s+)?(?:de\s+la|de\s+el|del|de|a|en)\s+(.+?)(?:\s+(?:por\s+mes|al\s+mes|mensual|anual|cada\s+mes|con|desde)\b|$)",
        mensaje,
        flags=re.IGNORECASE,
    )
    if m1:
        cand = m1.group(1).strip(" .,-")
        if cand:
            return cand

    m2 = re.search(
        r"me\s+suscrib[ií]\s+a\s+(.+?)(?:\s+(?:por\s+\$?[\d\.,]+|por\s+mes|al\s+mes|mensual|anual|con|desde)\b|$)",
        mensaje,
        flags=re.IGNORECASE,
    )
    if m2:
        cand = m2.group(1).strip(" .,-")
        if cand:
            return cand

    m3 = re.search(
        r"(?:di\s+de\s+baja|dar\s+de\s+baja|baja\s+de|cancel[eé]|cancele|ya\s+no\s+pago\s+m[aá]s|me\s+desuscrib[ií])\s+(?:la\s+suscripci[oó]n\s+a\s+|a\s+|el\s+|la\s+)?([^,\.]+?)(?:\s+por\s+favor|$)",
        mensaje,
        flags=re.IGNORECASE,
    )
    if m3:
        cand = m3.group(1).strip(" .,-")
        if cand:
            return cand

    m4 = re.search(
        r"(?:aument[oó]|aumento|subi[oó]|subio|cambi[oó]\s+de\s+precio)\s+(?:el\s+|la\s+)?([^,\.]+?)(?:,|\s+ahora|\s+a\s+|\s+subio|$)",
        mensaje,
        flags=re.IGNORECASE,
    )
    if m4:
        cand = m4.group(1).strip(" .,-")
        if cand:
            return cand

    return None


def _extraer_monto_y_moneda_suscripcion(mensaje: str) -> tuple[Decimal | None, str]:
    norm = normalizar_texto(mensaje)
    moneda = "USD" if any(w in norm for w in ["dolar", "dolares", "dólares", "usd", "us$"]) else "ARS"
    m = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*(?:d[oó]lares|usd|pesos)?\b", mensaje, flags=re.IGNORECASE)
    if m:
        monto = _parsear_monto_argentino(m.group(1))
        return monto, moneda
    return None, moneda


def _resolver_fecha_transaccion(fecha_val: str | None) -> date:
    fecha_obj, _ = _resolver_y_validar_fecha(fecha_val)
    return fecha_obj
