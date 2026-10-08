"""
app/routers/whatsapp/pdf_documento.py
Lectura, parsing y verificación determinística de texto en documentos PDF para WhatsApp.
Utiliza pypdf para extracción de texto y expresiones regulares para control de importes y fechas.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import io
import re
from typing import Any
import pypdf
import structlog

logger = structlog.get_logger(__name__)


def leer_texto_pdf(pdf_bytes: bytes, max_paginas: int = 6) -> tuple[str | None, str | None]:
    """
    Extrae el texto de un archivo PDF recibido en bytes.
    - Si está cifrado, prueba con clave vacía; si no abre: "PDF_CON_CLAVE".
    - Más de 6 páginas: "DEMASIADAS_PAGINAS".
    - Menos de 100 caracteres sin espacios en total: "SIN_TEXTO".
    - Cualquier otra excepción: "PDF_ILEGIBLE".
    - El texto son las páginas unidas con "\\n", recortado a 20.000 caracteres.
    Retorna (texto, None) o (None, motivo_error).
    """
    if not pdf_bytes:
        return None, "PDF_ILEGIBLE"

    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))

        if reader.is_encrypted:
            try:
                res_decrypt = reader.decrypt("")
                if res_decrypt == 0:
                    return None, "PDF_CON_CLAVE"
            except Exception:
                return None, "PDF_CON_CLAVE"

        try:
            total_paginas = len(reader.pages)
        except Exception:
            if getattr(reader, "is_encrypted", False):
                return None, "PDF_CON_CLAVE"
            return None, "PDF_ILEGIBLE"

        if total_paginas > max_paginas:
            return None, "DEMASIADAS_PAGINAS"

        textos_paginas: list[str] = []
        for p in reader.pages:
            try:
                t = p.extract_text() or ""
                textos_paginas.append(t)
            except Exception:
                if getattr(reader, "is_encrypted", False):
                    return None, "PDF_CON_CLAVE"
                return None, "PDF_ILEGIBLE"

        texto_unido = "\n".join(textos_paginas)
        chars_sin_espacios = re.sub(r"\s+", "", texto_unido)
        if len(chars_sin_espacios) < 100:
            return None, "SIN_TEXTO"

        texto_recortado = texto_unido[:20000]
        return texto_recortado, None

    except Exception:
        logger.exception("Error al leer documento PDF con pypdf")
        return None, "PDF_ILEGIBLE"


def montos_en_texto(texto: str) -> set[Decimal]:
    """
    Extrae del texto todos los importes con formato argentino:
    (\\d{1,3}(\\.\\d{3})+|\\d+),\\d{2}
    sin importar el $ ni los asteriscos de adelante.
    Retorna un set con los montos como Decimal.
    """
    if not texto:
        return set()

    patron = re.compile(r'(?<![\d.])(?:(\d{1,3}(?:\.\d{3})+)|(\d+)),(\d{2})(?!\d)')
    montos: set[Decimal] = set()

    for m in patron.finditer(texto):
        miles_part = m.group(1)
        entero_simple = m.group(2)
        dec_part = m.group(3)

        if miles_part is not None:
            entero = miles_part.replace(".", "")
        else:
            entero = entero_simple or "0"

        try:
            monto_dec = Decimal(f"{entero}.{dec_part}")
            montos.add(monto_dec)
        except (InvalidOperation, ValueError):
            pass

    return montos


def fechas_en_texto(texto: str) -> set[date]:
    """
    Extrae del texto todas las fechas dd/mm/aaaa válidas.
    Retorna un set con las fechas como date.
    """
    if not texto:
        return set()

    patron = re.compile(r'(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})(?!\d)')
    fechas: set[date] = set()

    for m in patron.finditer(texto):
        try:
            dia = int(m.group(1))
            mes = int(m.group(2))
            anio = int(m.group(3))
            d = date(anio, mes, dia)
            fechas.add(d)
        except (ValueError, TypeError):
            pass

    return fechas


def verificar_extraccion_contra_texto(resultado: Any, texto: str) -> bool:
    """
    Verifica que la extracción corresponda fidedignamente al texto del PDF.
    Es verdadero solo si:
    - todos los montos de movimientos y cuotas están en montos_en_texto;
    - el vencimiento y las fechas de las cuotas (si hay) están en fechas_en_texto.
    """
    if not resultado:
        return False

    montos = montos_en_texto(texto)
    fechas = fechas_en_texto(texto)

    movimientos = getattr(resultado, "movimientos", None)
    if not movimientos:
        return False

    for mov in movimientos:
        monto = getattr(mov, "monto", None)
        if monto is None or not any(monto == m_txt for m_txt in montos):
            return False

    vencimiento = getattr(resultado, "vencimiento", None)
    if vencimiento is not None:
        if isinstance(vencimiento, str):
            try:
                vencimiento = date.fromisoformat(vencimiento)
            except ValueError:
                return False
        if vencimiento not in fechas:
            return False

    cuotas = getattr(resultado, "cuotas", None) or []
    for c in cuotas:
        if isinstance(c, tuple) and len(c) == 2:
            c_venc, c_monto = c
        elif isinstance(c, dict):
            c_venc = c.get("vencimiento")
            c_monto = c.get("monto")
        else:
            c_venc = getattr(c, "vencimiento", None)
            c_monto = getattr(c, "monto", None)

        if isinstance(c_venc, str):
            try:
                c_venc = date.fromisoformat(c_venc)
            except ValueError:
                return False

        if c_venc is None or c_venc not in fechas:
            return False

        if c_monto is None:
            return False
        if not isinstance(c_monto, Decimal):
            try:
                c_monto = Decimal(str(c_monto))
            except (InvalidOperation, ValueError):
                return False

        if not any(c_monto == m_txt for m_txt in montos):
            return False

    return True


def fecha_solo_fiscal(fecha: date | str | None, texto: str) -> bool:
    """
    Devuelve True si cada aparición de la fecha (dd/mm/aaaa) en el texto tiene,
    en los 60 caracteres anteriores, una coincidencia de C.E.S.P. o C.A.E., sin importar mayúsculas.
    Si la fecha no aparece en el texto: False.
    """
    if not fecha or not texto:
        return False

    if isinstance(fecha, date):
        d_str = f"{fecha.day:02d}/{fecha.month:02d}/{fecha.year:04d}"
    elif isinstance(fecha, str):
        s = fecha.strip()
        m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
        m_arg = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", s)
        if m_iso:
            d_str = f"{int(m_iso.group(3)):02d}/{int(m_iso.group(2)):02d}/{int(m_iso.group(1)):04d}"
        elif m_arg:
            d_str = f"{int(m_arg.group(1)):02d}/{int(m_arg.group(2)):02d}/{int(m_arg.group(3)):04d}"
        else:
            d_str = s
    else:
        return False

    patron_fecha = re.compile(rf"(?<!\d){re.escape(d_str)}(?!\d)")
    matches = list(patron_fecha.finditer(texto))
    if not matches:
        return False

    patron_fiscal = re.compile(r"\bC\.?\s*E\.?\s*S\.?\s*P\b|\bC\.?\s*A\.?\s*E\b", re.IGNORECASE)

    for m in matches:
        start_idx = m.start()
        ventana = texto[max(0, start_idx - 60) : start_idx]
        if not patron_fiscal.search(ventana):
            return False

    return True

