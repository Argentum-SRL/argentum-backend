# -*- coding: utf-8 -*-
"""
Detección, parsing determinístico y extracción de transferencias, extracciones y depósitos
a partir del texto libre del mensaje de WhatsApp.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal

from app.core.jerga import (
    PALABRAS_DOLARES,
    PALABRAS_PESOS,
    monedas_dolares_rx,
    monedas_pesos_rx,
    sufijo_multiplicador_rx,
)
from app.routers.whatsapp.montos_palabras import buscar_monto, monto_en_palabras

_DIAS_SEMANA = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}
_MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
_PREFIXES_CUENTA = [
    "mi cuenta del ", "mi cuenta de ", "la cuenta del ", "la cuenta de ",
    "la billetera del ", "la billetera de ", "la de ", "el de ",
    "mi ", "la ", "el ", "los ", "las ",
]


def normalizar_basico(texto: str) -> str:
    """Normaliza quitando tildes, pasando a minúsculas y colapsando espacios."""
    if not texto:
        return ""
    s = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    s = s.lower()
    return " ".join(s.split())


def extraer_fecha(texto: str, hoy: date) -> tuple[date | None, str]:
    """
    Busca y extrae del texto fechas relativas o absolutas, retornando (fecha, texto_sin_fecha).
    """
    if not texto:
        return None, ""
    norm = normalizar_basico(texto)

    # 1. Fechas relativas fijas
    rel_pats = [
        (r"\bantes\s+de\s+ayer\b", hoy - timedelta(days=2)),
        (r"\banteayer\b", hoy - timedelta(days=2)),
        (r"\bayer\b", hoy - timedelta(days=1)),
        (r"\bhoy\b", hoy),
    ]
    for pat, f_val in rel_pats:
        m = re.search(pat, norm)
        if m:
            sin = norm[:m.start()] + " " + norm[m.end():]
            return f_val, " ".join(sin.split())

    # 2. Días de la semana ("el lunes" ... "el domingo", con o sin "pasado")
    m_sem = re.search(
        r"\bel\s+(lunes|martes|miercoles|jueves|viernes|sabado|domingo)(?:\s+pasado)?\b",
        norm,
    )
    if m_sem:
        dia_nom = m_sem.group(1)
        target_weekday = _DIAS_SEMANA[dia_nom]
        delta = (hoy.weekday() - target_weekday) % 7
        if delta == 0:
            delta = 7
        f_val = hoy - timedelta(days=delta)
        sin = norm[:m_sem.start()] + " " + norm[m_sem.end():]
        return f_val, " ".join(sin.split())

    # 3. Formato numérico "el D/M" o "el D/M/AAAA"
    m_num = re.search(r"\bel\s+(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", norm)
    if m_num:
        dia = int(m_num.group(1))
        mes = int(m_num.group(2))
        anio_raw = m_num.group(3)
        if anio_raw:
            anio = int(anio_raw)
            if anio < 100:
                anio += 2000
        else:
            anio = hoy.year
            try:
                f_cand = date(anio, mes, dia)
                if (f_cand - hoy).days > 31:
                    anio -= 1
            except ValueError:
                pass
        try:
            f_val = date(anio, mes, dia)
            sin = norm[:m_num.start()] + " " + norm[m_num.end():]
            return f_val, " ".join(sin.split())
        except ValueError:
            pass

    # 4. Formato textual "el D de <mes>" con "de AAAA" opcional
    m_text = re.search(r"\bel\s+(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?\b", norm)
    if m_text:
        dia = int(m_text.group(1))
        mes_nom = m_text.group(2)
        anio_raw = m_text.group(3)
        if mes_nom in _MESES:
            mes = _MESES.index(mes_nom) + 1
            if anio_raw:
                anio = int(anio_raw)
            else:
                anio = hoy.year
                try:
                    f_cand = date(anio, mes, dia)
                    if (f_cand - hoy).days > 31:
                        anio -= 1
                except ValueError:
                    pass
            try:
                f_val = date(anio, mes, dia)
                sin = norm[:m_text.start()] + " " + norm[m_text.end():]
                return f_val, " ".join(sin.split())
            except ValueError:
                pass

    return None, " ".join(norm.split())


def _limpiar_nombre_cuenta(nombre: str) -> str:
    s = normalizar_basico(nombre)
    s = re.sub(r"\s+y$", "", s)
    cambio = True
    while cambio:
        cambio = False
        for pref in _PREFIXES_CUENTA:
            if s.startswith(pref):
                s = s[len(pref):].strip()
                cambio = True
                break
    return s.strip()


def _extraer_monto_y_moneda(t: str) -> tuple[Decimal | None, str | None, str]:
    """Extrae el monto y la moneda escrita justo después del monto."""
    # 1. Numérico
    pat_num = re.search(
        rf"(?:\$|\b)\d+(?:[.,]\d+)*(?:\s*{sufijo_multiplicador_rx()})?\b",
        t,
        re.IGNORECASE,
    )
    if pat_num:
        cand = pat_num.group(0).strip()
        val, _ = buscar_monto(cand)
        if val is not None and val > 0:
            moneda = None
            resto = t[pat_num.end():]
            m_mon = re.match(
                rf"^\s+({monedas_dolares_rx()}|{monedas_pesos_rx()})\b",
                resto,
                re.IGNORECASE,
            )
            end_pos = pat_num.end()
            if m_mon:
                w = m_mon.group(1).lower()
                if w in PALABRAS_DOLARES:
                    moneda = "USD"
                else:
                    moneda = "ARS"
                end_pos += m_mon.end()
            sin = t[:pat_num.start()] + " " + t[end_pos:]
            return val, moneda, " ".join(sin.split())

    # 2. Palabras
    palabras = t.split()
    n = len(palabras)
    for i in range(n):
        for j in range(n, i, -1):
            sub_words = palabras[i:j]
            val = monto_en_palabras(" ".join(sub_words))
            if val is not None and val > 0:
                moneda = None
                end_idx = j
                if j < n and palabras[j] in (*PALABRAS_DOLARES, *PALABRAS_PESOS):
                    w = palabras[j]
                    if w in PALABRAS_DOLARES:
                        moneda = "USD"
                    else:
                        moneda = "ARS"
                    end_idx += 1
                sin = " ".join(palabras[:i] + palabras[end_idx:])
                return val, moneda, sin

    return None, None, t


def _procesar_parte_transferencia(
    parte: str,
    hoy: date,
    contexto_verbo_fuerte: bool = False,
) -> dict | None:
    t = parte

    # 1. Sacar fecha
    fecha, t = extraer_fecha(t, hoy)

    # 2. Sacar comisión: "con|mas|y N (de )?comision"
    comision = None
    m_com = re.search(r"\b(?:con|mas|y)\s+(.+?)\s+(?:de\s+)?comision\b", t)
    if m_com:
        com_txt = m_com.group(1).strip()
        c_val, _ = buscar_monto(com_txt)
        if c_val is not None:
            comision = c_val
            t = t[:m_com.start()] + " " + t[m_com.end():]
            t = " ".join(t.split())

    # 3. Monto y moneda
    monto, moneda, t = _extraer_monto_y_moneda(t)

    # 4. Cuentas y tipos
    t_limpio = re.sub(r"\b(?:en\s+el\s+cajero|del\s+cajero|al\s+cajero)\b", "", t)
    t_limpio = re.sub(r"\s+y\s*$", "", t_limpio)
    t_limpio = " ".join(t_limpio.split())

    origen = None
    destino = None
    tipo = "transferencia"

    # Caso "saque N de X y (los )?(meti|puse|pase|deposite|cargue) en|a|al Y"
    m_saque_meti = re.search(
        r"\b(?:saque|retire|extraje)\b.*?\b(?:de|del|desde)\s+(.+?)\s+y\s+"
        r"(?:los\s+)?(?:meti|puse|pase|deposite|cargue)\s+(?:en|a|al)\s+(.+)$",
        t,
    )
    if m_saque_meti:
        origen = _limpiar_nombre_cuenta(m_saque_meti.group(1))
        destino = _limpiar_nombre_cuenta(m_saque_meti.group(2))
        tipo = "transferencia"
        return {
            "monto": monto,
            "moneda": moneda,
            "origen": origen,
            "destino": destino,
            "tipo": tipo,
            "fecha": fecha,
            "comision": comision,
        }

    # Detectar posiciones relativas de conectores
    pos_de = None
    m_de = re.search(r"\b(desde|del|de)\s+", t_limpio)
    if m_de:
        pos_de = m_de.start()

    pos_a = None
    m_a = re.search(r"\b(a|al|hacia|para|en)\s+", t_limpio)
    if m_a:
        pos_a = m_a.start()

    if pos_a is not None and pos_de is not None and pos_a < pos_de:
        m_dest_orig = re.search(
            r"\b(?:a|al|en)\s+(.+?)\s+(?:desde|del|de)\s+(.+)$",
            t_limpio,
        )
        if m_dest_orig:
            destino = _limpiar_nombre_cuenta(m_dest_orig.group(1))
            origen = _limpiar_nombre_cuenta(m_dest_orig.group(2))
    elif pos_de is not None and pos_a is not None and pos_de < pos_a:
        m_orig_dest = re.search(
            r"\b(?:desde|del|de)\s+(.+?)\s+(?:a|al|hacia|para)\s+(.+)$",
            t_limpio,
        )
        if m_orig_dest:
            origen = _limpiar_nombre_cuenta(m_orig_dest.group(1))
            destino = _limpiar_nombre_cuenta(m_orig_dest.group(2))
    else:
        m_dest = re.search(r"\b(?:a|al|en|hacia|para)\s+(.+)$", t_limpio)
        if m_dest:
            destino = _limpiar_nombre_cuenta(m_dest.group(1))
        m_orig = re.search(r"\b(?:desde|del|de)\s+(.+)$", t_limpio)
        if m_orig:
            origen = _limpiar_nombre_cuenta(m_orig.group(1))

    # Extracciones y depósitos
    tiene_verb_extraccion = bool(re.search(
        r"\b(?:saque|retire|extraje|extraccion)\b",
        t,
    ))
    tiene_verb_deposito = bool(re.search(
        r"\b(?:deposite|deposito)\b",
        t,
    ))

    if tiene_verb_extraccion:
        if destino is None or re.search(r"\b(?:cajero|banco|plata|efectivo)\b", t):
            tipo = "extraccion"
            destino = None
            if origen and "cajero" in origen:
                origen = None

    elif tiene_verb_deposito:
        if destino and not origen:
            tipo = "deposito"

    verbos_fuertes = bool(re.search(
        r"\b(?:pase|me\s+pase|transferi|me\s+transferi|transferencia|mande|movi|envie|gire)\b",
        t,
    ))
    verbos_dos_cuentas = bool(re.search(
        r"\b(?:cargue|ingrese|meti|puse)\b",
        t,
    ))

    if tipo == "extraccion":
        pass
    elif tipo == "deposito":
        if not destino:
            return None
    elif verbos_dos_cuentas:
        if not (origen and destino):
            return None
    elif verbos_fuertes or contexto_verbo_fuerte:
        if not (origen or destino):
            return None
    else:
        # Sin verbo: debe tener las dos cuentas
        if not (origen and destino):
            return None

    if origen:
        origen = _limpiar_nombre_cuenta(origen)
        if not origen:
            origen = None
    if destino:
        destino = _limpiar_nombre_cuenta(destino)
        if not destino:
            destino = None

    return {
        "monto": monto,
        "moneda": moneda,
        "origen": origen,
        "destino": destino,
        "tipo": tipo,
        "fecha": fecha,
        "comision": comision,
    }


def extraer_transferencias(texto: str, hoy: date) -> list[dict] | None:
    """
    Interpreta transferencias, extracciones y depósitos de un mensaje.
    Retorna lista de dicts con claves:
    monto, moneda, origen, destino, tipo, fecha, comision.
    Retorna None si el mensaje no es puramente de transferencias/extracciones/depósitos.
    """
    if not texto:
        return None
    raw_norm = normalizar_basico(texto)

    # a) Exclusiones globales
    if re.search(
        r"\b(?:gaste|pague|compre|me\s+compre|abone|cobre|me\s+cobraron|me\s+pagaron)\b",
        raw_norm,
    ):
        return None
    if re.search(
        r"\ble\s+(?:pase|transferi|mande|pague|deposite|envie|gire)\b",
        raw_norm,
    ):
        return None
    if re.search(r"\bsube\b", raw_norm):
        return None

    # b) Partir en partes por coma, punto y coma, salto de línea y " y "
    partes_raw = re.split(
        r"(?:[\n;]|(?<!\d),(?!\d)|\s+y\s+"
        r"(?!(?:medio|media|un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve)\b))",
        raw_norm,
    )
    partes_raw = [p.strip() for p in partes_raw if p.strip()]
    if not partes_raw:
        return None

    # Unir partes sin monto con " y "
    partes = []
    i = 0
    while i < len(partes_raw):
        p = partes_raw[i]
        m, _ = buscar_monto(p)
        if m is None:
            if partes:
                partes[-1] = partes[-1] + " y " + p
            elif i + 1 < len(partes_raw):
                partes_raw[i + 1] = p + " y " + partes_raw[i + 1]
            else:
                partes.append(p)
        else:
            partes.append(p)
        i += 1

    primer_verbo_fuerte = bool(re.search(
        r"\b(?:pase|me\s+pase|transferi|me\s+transferi|transferencia|mande|movi|envie|gire)\b",
        raw_norm,
    ))

    resultados = []
    for parte in partes:
        item = _procesar_parte_transferencia(
            parte,
            hoy,
            contexto_verbo_fuerte=primer_verbo_fuerte,
        )
        if item is None:
            return None
        resultados.append(item)

    return resultados
