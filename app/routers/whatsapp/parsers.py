"""
Parsers y formateadores determinísticos puros para el flujo de WhatsApp IA.
Funciones puras (sin I/O de red ni base de datos).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from app.core.catalogo_suscripciones import identificar_servicio_en_texto
from app.core.jerga import (
    PALABRAS_DOLARES,
    monedas_dolares_rx,
    monedas_pesos_rx,
    multiplicador_de,
    sufijo_multiplicador_rx,
)
from app.core.reglas_texto_wpp import fecha_corta_wpp
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
    m_mult = re.match(
        rf"^([0-9]+(?:[.,][0-9]+)?)\s*({sufijo_multiplicador_rx()})$",
        t,
    )
    if m_mult:
        mult = multiplicador_de(m_mult.group(2))
        if mult is not None:
            val = float(m_mult.group(1).replace(",", ".")) * mult
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

    # Caso 1: "N cuotas de X", "N cuotas sin interés de X", "N cuotas fijas de X", "X por mes/cuota"
    suf_rx = sufijo_multiplicador_rx()
    monto_rx = rf"\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*{suf_rx})?"
    pat_de = re.search(
        rf"(?:en\s+)?(\d+)\s*(?:cuotas?|pagos?)"
        rf"(?:\s+(?:sin\s+interes|fijas?))?\s+de\s+"
        rf"(?:cada\s+una\s+de\s+)?({monto_rx})(?:\b|$)",
        m_norm,
    )
    if pat_de:
        cant = int(pat_de.group(1))
        if cant < 1 or cant > 48:
            return cant, None, None, False, "Las cuotas tienen que ser entre 1 y 48."
        m_str = pat_de.group(2).strip()
        m_val = _parsear_monto_texto_cuota(m_str)
        if m_val is None and monto_ia is not None:
            m_val = monto_ia
        if m_val is not None:
            monto_cuota = m_val
            monto_total = Decimal(str(cant)) * monto_cuota
            return cant, monto_cuota, monto_total, False, None

    pat_gen = re.search(r"(?:en\s+)?(\d+)\s*(?:cuotas?|pagos?)", m_norm)
    if pat_gen:
        cant_gen = int(pat_gen.group(1))
        pat_cada = re.search(
            rf"({monto_rx})\s+(?:por\s+mes|cada\s+una|por\s+cuota)\b",
            m_norm,
        )
        if pat_cada:
            if cant_gen < 1 or cant_gen > 48:
                return (
                    cant_gen,
                    None,
                    None,
                    False,
                    "Las cuotas tienen que ser entre 1 y 48.",
                )
            m_val = _parsear_monto_texto_cuota(pat_cada.group(1).strip())
            if m_val is None and monto_ia is not None:
                m_val = monto_ia
            if m_val is not None:
                return (
                    cant_gen,
                    m_val,
                    Decimal(str(cant_gen)) * m_val,
                    False,
                    None,
                )

    # Caso 2: "X en total", "total X", "por un total de X", "X en N cuotas" -> X es el total
    if pat_gen:
        cant_tot = int(pat_gen.group(1))
        pat_tot_exp = re.search(
            rf"({monto_rx})\s+en\s+total\b"
            rf"|(?:por\s+un\s+total\s+de|total(?:\s+de)?)\s+({monto_rx})(?:\b|$)",
            m_norm,
        )
        if pat_tot_exp:
            if cant_tot < 1 or cant_tot > 48:
                return (
                    cant_tot,
                    None,
                    None,
                    False,
                    "Las cuotas tienen que ser entre 1 y 48.",
                )
            raw_tot = (pat_tot_exp.group(1) or pat_tot_exp.group(2) or "").strip()
            m_val = _parsear_monto_texto_cuota(raw_tot)
            if m_val is None and monto_ia is not None:
                m_val = monto_ia
            if m_val is not None:
                return (
                    cant_tot,
                    round(m_val / Decimal(str(cant_tot)), 2),
                    m_val,
                    False,
                    None,
                )

    pat_en = re.search(
        rf"({monto_rx})\s+"
        rf"(?:(?:a\s+pagar|(?!por\s+mes|cada\s+una|por\s+cuota)[a-z0-9]+)\s+){{0,4}}?"
        rf"en\s+(\d+)\s*(?:cuotas?|pagos?)(?:\b|$)",
        m_norm,
    )
    if pat_en:
        cant = int(pat_en.group(2))
        if cant < 1 or cant > 48:
            return cant, None, None, False, "Las cuotas tienen que ser entre 1 y 48."
        m_str = pat_en.group(1).strip()
        m_val = _parsear_monto_texto_cuota(m_str)
        if m_val is None and monto_ia is not None:
            m_val = monto_ia
        if m_val is not None:
            monto_total = m_val
            monto_cuota = round(monto_total / Decimal(str(cant)), 2)
            return cant, monto_cuota, monto_total, False, None

    # Caso 3: Menciona cuotas ("X cuotas") pero sin encajar claramente en Caso 1 ni Caso 2
    if pat_gen:
        cant = int(pat_gen.group(1))
        if cant < 1 or cant > 48:
            return cant, None, None, False, "Las cuotas tienen que ser entre 1 y 48."
        if cant > 1 and monto_ia is not None:
            return cant, None, None, True, None

    # Caso 4: No menciona cuotas (1 pago)
    if monto_ia is not None:
        return 1, monto_ia, monto_ia, False, None

    return 1, None, None, False, None


def _parsear_fecha_texto(texto: str | None) -> date | None:
    """
    Parsea determinísticamente fechas en formatos habituales argentinos:
    - ISO: '2026-09-18'
    - Numérico: '27/09', '18/09/2026', 'del 27/09'
    - Textual: 'el 18 de septiembre', '23 de septiembre'
    - Relativo: 'hoy', 'ayer', 'anteayer'
    """
    if not texto:
        return None
    hoy = hoy_argentina()
    t = str(texto).strip().lower()

    if t in ("hoy", "de hoy"):
        return hoy
    if t in ("ayer", "de ayer"):
        return hoy - timedelta(days=1)
    if t in ("anteayer", "antes de ayer"):
        return hoy - timedelta(days=2)

    # 1. ISO format
    try:
        return date.fromisoformat(t)
    except Exception:
        pass

    # 2. Formato textual: 'el 18 de septiembre', 'del 27 de septiembre'
    m_textual = re.search(r"(?:el\s+|del\s+)?\b(\d{1,2})\s+de\s+([a-záéíóú]+)(?:\s+de\s+(\d{4}))?\b", t)
    if m_textual:
        dia = int(m_textual.group(1))
        mes_raw = normalizar_texto(m_textual.group(2))
        anio = int(m_textual.group(3)) if m_textual.group(3) else hoy.year
        if mes_raw in _MESES_RIOPLATENSE:
            mes = _MESES_RIOPLATENSE.index(mes_raw) + 1
            try:
                return date(anio, mes, dia)
            except ValueError:
                pass

    # 3. Formato numérico: 'del 27/09', '27/09', '27/09/2026'
    m_num = re.search(r"(?:el\s+|del\s+)?\b(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\b", t)
    if m_num:
        dia = int(m_num.group(1))
        mes = int(m_num.group(2))
        anio = int(m_num.group(3)) if m_num.group(3) else hoy.year
        if anio < 100:
            anio += 2000
        try:
            return date(anio, mes, dia)
        except ValueError:
            pass

    return None


def _resolver_y_validar_fecha(fecha_val: str | date | None) -> tuple[date, str | None]:
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

    if isinstance(fecha_val, date):
        fecha_candidata = fecha_val
    else:
        fecha_candidata = _parsear_fecha_texto(str(fecha_val))
        if fecha_candidata is None:
            return hoy, None

    limite_antiguedad = hoy - timedelta(days=60)
    if fecha_candidata > hoy:
        return hoy, "Todavía no pasó esa fecha: va con fecha de hoy."
    elif fecha_candidata < limite_antiguedad:
        return (
            hoy,
            "Solo anoto hasta 60 días para atrás: va con fecha de hoy.",
        )
    else:
        return fecha_candidata, None


def _formatear_fecha_natural(fecha_obj: date) -> str | None:
    """Formatea una fecha con fecha_corta_wpp respecto a hoy."""
    res = fecha_corta_wpp(fecha_obj, hoy_argentina())
    return res or None


def _parsear_monto_argentino(texto: str) -> Decimal | None:
    if not texto:
        return None
    limpio = texto.strip().lower()
    limpio = limpio.replace("$", "").replace("ars", "").replace("usd", "").strip()
    multiplicador = Decimal("1")
    m_mult = re.search(
        rf"(?:(?<=\d)|(?<=\s)|^)\s*({sufijo_multiplicador_rx()})$",
        limpio,
    )
    if m_mult:
        mult = multiplicador_de(m_mult.group(1))
        if mult is not None:
            multiplicador = Decimal(str(mult))
            limpio = limpio[: m_mult.start()].strip()

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

    mon_rx = rf"(?:{monedas_dolares_rx()}|{monedas_pesos_rx()}|us\$)"
    suf_rx = sufijo_multiplicador_rx()
    m1 = re.search(
        rf"(?:empec[eé]|empece)\s+a\s+pagar\s+"
        rf"(?:\$?\s*[\d\.,]+(?:\s*{suf_rx})?\s*{mon_rx}?\s+)?"
        r"(?:de\s+la|de\s+el|del|de|a|en)\s+(.+?)"
        r"(?:\s+(?:por\s+mes|al\s+mes|mensual|anual|cada\s+mes|con|desde)\b|$)",
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
    es_usd = any(w in norm for w in (*PALABRAS_DOLARES, "us$"))
    moneda = "USD" if es_usd else "ARS"
    suf_rx = sufijo_multiplicador_rx()
    mon_rx = rf"(?:{monedas_dolares_rx()}|{monedas_pesos_rx()}|us\$)"
    m = re.search(
        rf"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*{suf_rx})?)\s*{mon_rx}?\b",
        mensaje,
        flags=re.IGNORECASE,
    )
    if m:
        monto = _parsear_monto_argentino(m.group(1))
        return monto, moneda
    return None, moneda


def _resolver_fecha_transaccion(fecha_val: str | None) -> date:
    fecha_obj, _ = _resolver_y_validar_fecha(fecha_val)
    return fecha_obj


def montos_de_dinero_en_texto(texto: str) -> list[str]:
    """
    Devuelve los montos con marca de dinero presentes en el texto:
    - '$' adelante, o seguidos de pesos, mil, lucas, luca, k, palos, dólares, dolares, usd o us$;
    - excluye fechas (27/09, 27 de septiembre) y lo que sigue a 'cuotas de' o 'cuota de'.
    """
    if not texto:
        return []

    # 1. Enmascarar fechas
    patron_fechas = [
        r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
        r"\b\d{1,2}\s+de\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)(?:\s+de\s+\d{2,4})?\b",
    ]
    t_enmascarado = texto
    for pf in patron_fechas:
        t_enmascarado = re.sub(pf, lambda m: " " * len(m.group(0)), t_enmascarado, flags=re.IGNORECASE)

    # 2. Enmascarar lo que sigue a 'cuotas de' o 'cuota de'
    sufijos = (
        rf"(?:{sufijo_multiplicador_rx()}|{monedas_pesos_rx()}|"
        rf"{monedas_dolares_rx()}|us\$)"
    )
    patron_cuotas_de = (
        r"\bcuotas?\s+de\s+(?:cada\s+una\s+de\s+)?"
        rf"(?:[\$€£]|us\$|usd)?\s*[\d\.,]+(?:\s*{sufijos})?"
    )
    t_enmascarado = re.sub(
        patron_cuotas_de,
        lambda m: " " * len(m.group(0)),
        t_enmascarado,
        flags=re.IGNORECASE,
    )

    # 3. Buscar montos con marca de dinero
    patron_monto = re.compile(
        rf"""
        (?:
            (?:(?:\$|us\$|u\$s)\s*\d+(?:[\.,]\d+)*(?:\s*{sufijos})?)
            |
            (?:\b\d+(?:[\.,]\d+)*\s*{sufijos}\b)
        )
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    encontrados = []
    for match in patron_monto.finditer(t_enmascarado):
        start, end = match.span()
        trozo_orig = texto[start:end].strip()
        if trozo_orig:
            encontrados.append(trozo_orig)

    return encontrados


def parsear_monto_marca(m_txt: str) -> Decimal | None:
    """Extrae el valor numérico Decimal de una expresión de monto con marca."""
    rx_mon = rf"(?:[\$€£]|us\$|{monedas_dolares_rx()}|{monedas_pesos_rx()})"
    t = re.sub(rx_mon, "", m_txt, flags=re.IGNORECASE).strip()
    return _parsear_monto_texto_cuota(t) or _parsear_monto_argentino(t)


def propagar_fechas_lote(entidades: dict) -> None:
    """
    En un mensaje con varios movimientos, un ítem que viene sin fecha toma
    la última fecha que apareció antes que él en el mensaje, recorriendo en orden
    el movimiento principal y después cada transacciones_adicionales.
    Si ningún ítem anterior tiene fecha, queda como hoy (o None).
    Un ítem que trae su propia fecha nunca se toca.
    Modifica el diccionario in-place.
    """
    if not isinstance(entidades, dict):
        return

    ultima_fecha = entidades.get("fecha") or None
    adicionales = entidades.get("transacciones_adicionales")
    if isinstance(adicionales, list):
        for adic in adicionales:
            if isinstance(adic, dict):
                fecha_adic = adic.get("fecha") or None
                if fecha_adic:
                    ultima_fecha = fecha_adic
                elif ultima_fecha:
                    adic["fecha"] = ultima_fecha

