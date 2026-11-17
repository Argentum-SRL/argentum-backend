"""Reglas de estilo y formato para los textos que el bot manda por WhatsApp."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import re
from typing import Any

LARGO_MAXIMO_LINEA_WPP = 90

_SIGLAS_EXACTAS = ("REM", "TNA", "CFT", "ARS", "USD", "Aviso:")

_RX_TUTEO = (
    r"\b(tienes|puedes|quieres|dime|escribe|env[ií]a|"
    r"elige|ingresa|intenta|verifica|debes)\b"
)

PROHIBIDAS: list[tuple[str, str]] = [
    (r"\bbilleteras?\b", "billetera"),
    (r"\bregistr\w*", "registrar"),
    (r"\bpor favor\b", "por favor"),
    (r"\bgestion\w*", "gestionar"),
    (r"\brealiz\w*", "realizar"),
    (r"\bcorresponde\w*", "corresponde"),
    (r"\boperaci[oó]n\w*", "operación"),
    (r"\binv[aá]lid\w*", "inválido"),
    (r"\bv[aá]lid[oa]s?\b", "válido"),
    (r"\belimin\w*", "eliminar"),
    (r"\banul\w*", "anular"),
    (r"\bdeshac\w*", "deshacer"),
    (r"\bdeshech\w*", "deshecha"),
    (r"\bdevoluci[oó]n\w*", "devolución"),
    (r"\bper[ií]odos?\b", "período"),
    (r"\bciclos?\b", "ciclo"),
    (r"\bbalance\b", "balance"),
    (r"\begresos?\b", "egreso"),
    (r"\bproyecci[oó]n\w*", "proyección"),
    (r"\bimporte\w*", "importe"),
    (r"\bpercepci[oó]n\w*", "percepción"),
    (r"\bcon [eé]xito\b", "con éxito"),
    (r"\bproces\w*", "procesar"),
    (r"\bAviso:", "Aviso:"),
    (r"\bREM\b", "REM"),
    (r"\bTNA\b", "TNA"),
    (r"\bCFT\b", "CFT"),
    (r"\bARS\b", "ARS"),
    (r"\bUSD\b", "USD"),
    (r"\bla web\b", "la web"),
    (r"\b(dimos|encontramos|anotamos|contactanos)\b", "plural"),
    (_RX_TUTEO, "tuteo"),
]

MESES = (
    "enero|febrero|marzo|abril|mayo|junio|"
    "julio|agosto|septiembre|octubre|noviembre|diciembre"
)

EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27bf\ufe0f]")

_RX_COMILLAS_SIMPLES = (
    r"(?<![A-Za-zÁÉÍÓÚáéíóúÑñ])'|'(?![A-Za-zÁÉÍÓÚáéíóúÑñ])"
)
_RX_FECHA_LARGA = rf"\b\d{{1,2}} de ({MESES})\b"


def _sin_cursiva(linea: str) -> str:
    """Saca los tramos en cursiva de WhatsApp (_texto_) para contar preguntas."""
    return re.sub(r"(?<![\w])_[^_\n]+_(?![\w])", "", linea)


def problemas_texto_wpp(texto: str) -> list[str]:
    """Devuelve los códigos de regla que rompe un texto pensado para WhatsApp."""
    out: list[str] = []
    lineas = texto.split("\n")

    # R1: ninguna línea puede pasar de 90 caracteres
    for i, linea in enumerate(lineas, 1):
        if len(linea) > LARGO_MAXIMO_LINEA_WPP:
            out.append(f"R1_LINEA_LARGA:{i}:{len(linea)}")

    # R2: palabras técnicas, robóticas, voseo roto o en plural
    for rx, nombre in PROHIBIDAS:
        flags = 0 if nombre in _SIGLAS_EXACTAS else re.IGNORECASE
        if re.search(rx, texto, flags=flags):
            out.append(f"R2_PALABRA:{nombre}")

    # R3 a R6: comillas, negrita, plural con paréntesis y rayas
    if re.search(r"[«»“”\"]", texto) or re.search(_RX_COMILLAS_SIMPLES, texto):
        out.append("R3_COMILLAS")
    if "*" in texto:
        out.append("R4_NEGRITA")
    if re.search(r"\((s|es)\)", texto):
        out.append("R5_PLURAL_PARENTESIS")
    if "—" in texto or "–" in texto:
        out.append("R6_RAYA")

    # R7: como máximo una pregunta y siempre al final (salvo opciones de respuesta)
    preguntas = [i for i, linea in enumerate(lineas) if "?" in _sin_cursiva(linea)]
    if sum(_sin_cursiva(linea).count("¿") for linea in lineas) > 1:
        out.append("R7_MAS_DE_UNA_PREGUNTA")
    if preguntas:
        for linea in lineas[preguntas[-1] + 1:]:
            if linea.strip() and not re.match(r"^(- |\d{1,2}\. |Sí: |No: )", linea):
                out.append("R7_PREGUNTA_NO_AL_FINAL")
                break

    # R8 a R10: avisos repetidos, centavos fuera de cuotas/facturas y formato de listas
    if texto.count("Ojo:") > 1:
        out.append("R8_OJO_REPETIDO")
    for linea in lineas:
        if (
            re.search(r"\$ ?[\d.]+,\d{2}\b", linea)
            and not re.search(r"cuota|factura", linea, re.IGNORECASE)
        ):
            out.append("R9_CENTAVOS")
            break
    if (
        re.search(r"(?m)^(• |\* |\d{1,2}\) )", texto)
        or re.search(r"\n\n(- |\d{1,2}\. )", texto)
    ):
        out.append("R10_LISTA")

    # R11 a R13: variables sin rellenar, fechas largas y emojis
    if re.search(r"\bNone\b|\bnan\b|\bnull\b|[{}]", texto):
        out.append("R11_SIN_RELLENAR")
    if (
        re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", texto)
        or re.search(_RX_FECHA_LARGA, texto)
        or re.search(r"\banteayer\b", texto, re.I)
    ):
        out.append("R12_FECHA")
    if EMOJI.search(texto):
        out.append("R13_EMOJI")

    return out


def _buscar_corte_linea(linea: str) -> tuple[str, str] | None:
    """Busca el mejor punto de corte para dejar el primer tramo en 90 caracteres o menos."""
    limite = LARGO_MAXIMO_LINEA_WPP + 1
    for sep in (": ", ". ", ", "):
        pos = linea.rfind(sep, 0, limite)
        if pos >= 0:
            primera = linea[:pos + 1]
            resto = linea[pos + 2:].lstrip()
            if primera and resto:
                return primera, resto

    pos_esp = linea.rfind(" ", 0, limite)
    if pos_esp > 0:
        primera = linea[:pos_esp].rstrip()
        resto = linea[pos_esp + 1:].lstrip()
        if primera and resto:
            return primera, resto

    return None


def _partir_una_linea(linea: str) -> list[str]:
    """Parte una línea de más de 90 caracteres repitiendo sobre la cola si hace falta."""
    partes: list[str] = []
    actual = linea
    while len(actual) > LARGO_MAXIMO_LINEA_WPP:
        corte = _buscar_corte_linea(actual)
        if corte is None:
            break
        primera, actual = corte
        partes.append(primera)
    partes.append(actual)
    return partes


def partir_lineas_wpp(texto: str) -> str:
    """Parte las líneas de un mensaje que pasen de 90 caracteres sin cambiar palabras."""
    if not texto:
        return texto
    resultado: list[str] = []
    for linea in texto.split("\n"):
        resultado.extend(_partir_una_linea(linea))
    return "\n".join(resultado)


def fecha_corta_wpp(fecha: date, hoy: date) -> str:
    """Formatea una fecha en estilo corto para WhatsApp respecto a hoy."""
    f = fecha.date() if isinstance(fecha, datetime) else fecha
    h = hoy.date() if isinstance(hoy, datetime) else hoy
    if f == h:
        return ""
    if f == h - timedelta(days=1):
        return "ayer"
    return f"{f.day}/{f.month:02d}"


def nombre_tarjeta_wpp(tarjeta: Any) -> str:
    """Devuelve el nombre visible de la tarjeta sin repetir palabras."""
    red_raw = getattr(tarjeta, "red", None)
    red_val = red_raw.value if hasattr(red_raw, "value") else (red_raw or "")
    red_txt = str(red_val).strip().capitalize()
    billetera = getattr(tarjeta, "billetera", None)
    nom_b = (getattr(billetera, "nombre", None) or "").strip() if billetera else ""
    nombre = (getattr(tarjeta, "nombre", None) or "").strip()
    palabras: list[str] = []
    vistas: set[str] = set()
    for parte in (red_txt, nom_b, nombre):
        for palabra in parte.split():
            clave = palabra.casefold()
            if clave not in vistas:
                vistas.add(clave)
                palabras.append(palabra)
    return " ".join(palabras)


def ejemplo_wpp(texto: str) -> str:
    """Envuelve un ejemplo o frase de la persona en cursiva de WhatsApp."""
    return f"_{texto}_"
