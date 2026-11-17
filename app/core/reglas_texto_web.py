"""Reglas de estilo y formato para los textos de la web (cartelitos y campanita).
Mismos códigos, mismo orden y mismas expresiones que src/utils/reglasTextoWeb.ts del frontend."""
from __future__ import annotations

from datetime import date, datetime
import re

LARGO_MAXIMO_TITULO_WEB = 90
MAXIMO_RENGLONES_WEB = 7

# Bordes de palabra con letras unicode (igual que (?<![\p{L}\p{N}_]) en el front).
_A = r"(?<![^\W])"
_Z = r"(?![^\W])"

# (expresión, nombre, distingue mayúsculas)
PALABRAS_WEB = [
    (r"con [eé]xito", "con éxito", False),
    (r"exitosamente", "exitosamente", False),
    (r"correctamente", "correctamente", False),
    (r"por favor", "por favor", False),
    (r"realiz\w*", "realizar", False),
    (r"gestion\w*", "gestionar", False),
    (r"proces\w*", "procesar", False),
    (r"operaci[oó]n\w*", "operación", False),
    (r"inv[aá]lid\w*", "inválido", False),
    (r"v[aá]lid[oa]s?", "válido", False),
    (r"error al", "error al", False),
    (r"(?:debe|debes|debés|deben)", "deber", False),
    (r"requerid\w*", "requerido", False),
    (r"exced\w*", "exceder", False),
    (r"ha sido|han sido", "pasiva", False),
    (r"intent[aá]", "intentá", False),
    (
        r"(?:tienes|puedes|quieres|dime|escribe|env[ií]a|"
        r"elige|ingresa|intenta|verifica)",
        "tuteo",
        False,
    ),
    (
        r"(?:pudimos|enviamos|mandamos|cambiamos|anotamos|encontramos|contactanos|registramos"
        r"|vamos a|anoté|saqué|guardé|borré|eliminé|voy a)",
        "primera persona",
        False,
    ),
    (r"la web", "la web", False),
    (r"REM", "REM", True),
    (r"TNA", "TNA", True),
    (r"CFT", "CFT", True),
    (r"ARS", "ARS", True),
    (r"USD", "USD", True),
]

_MESES = (
    "enero|febrero|marzo|abril|mayo|junio|"
    "julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre"
)


def _es_item(linea: str) -> bool:
    return linea.startswith("- ")


def fecha_dm(fecha: date | datetime) -> str:
    """Formatea una fecha como d/mm (día sin cero, mes con dos dígitos)."""
    f = fecha.date() if isinstance(fecha, datetime) else fecha
    return f"{f.day}/{f.month:02d}"


def problemas_texto_web(texto: str) -> list[str]:
    """Devuelve la lista de códigos de problemas del texto (vacía si cumple las reglas)."""
    res: list[str] = []
    lineas = [l.strip() for l in texto.replace("\r\n", "\n").split("\n")]
    lineas = [l for l in lineas if l]
    if lineas and len(lineas[0]) > LARGO_MAXIMO_TITULO_WEB:
        res.append(f"W1_TITULO_LARGO:{len(lineas[0])}")
    if lineas and re.match(r"listo(?![^\W])", lineas[0], re.IGNORECASE):
        res.append("W2_PALABRA:listo")
    for expr, nombre, exacta in PALABRAS_WEB:
        flags = 0 if exacta else re.IGNORECASE
        if re.search(_A + expr + _Z, texto, flags):
            res.append(f"W2_PALABRA:{nombre}")
    if (
        re.search(r"[«»“”\"]", texto)
        or re.search(r"(?<![^\W\d_])'|'(?![^\W\d_])", texto)
    ):
        res.append("W3_COMILLAS")
    if "*" in texto or "_" in texto:
        res.append("W4_MARCAS")
    if "(s)" in texto or "(es)" in texto:
        res.append("W5_PLURAL_PARENTESIS")
    if "—" in texto or "–" in texto:
        res.append("W6_RAYA")
    for i, l in enumerate(lineas, start=1):
        if not _es_item(l) and not re.search(r"[.?!:]$", l):
            res.append(f"W7_SIN_PUNTO:{i}")
    if (
        re.search(_A + r"(?:None|nan|NaN|null|undefined)" + _Z, texto)
        or "{" in texto
        or "}" in texto
    ):
        res.append("W8_SIN_RELLENAR")
    if (
        re.search(r"(?<!\d)\d{1,2}/\d{1,2}/\d{2,4}(?!\d)", texto)
        or re.search(r"(?<!\d)0\d/\d{1,2}(?!\d)", texto)
        or re.search(r"(?<!\d)\d{1,2} de (?:" + _MESES + r")", texto, re.IGNORECASE)
        or re.search(_A + r"anteayer" + _Z, texto, re.IGNORECASE)
    ):
        res.append("W9_FECHA")
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿️]", texto):
        res.append("W10_EMOJI")
    if re.search(r"https?://|www\.|\.com(?![^\W])", texto, re.IGNORECASE):
        res.append("W11_LINK")
    if len(lineas) > MAXIMO_RENGLONES_WEB:
        res.append("W12_RENGLONES")
    if any(re.match(r"(?:• |\* |\d{1,2}\) )", l) for l in lineas):
        res.append("W13_LISTA")
    return res
