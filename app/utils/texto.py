"""
app/utils/texto.py — Utilidades compartidas de normalización de texto.
"""
import re
import unicodedata


def normalizar_texto(texto: str | None) -> str:
    """
    Normalización única y compartida para matching de categorías, subcategorías y entidades:
    - Trim y lowercase.
    - Eliminación de acentos y diacríticos (NFD + descarte de marcas Mn).
    - Normalización de variantes de barra (" / " y "/" a "/").
    - Tratamiento de ' y ' como separador de token (reemplazado por espacio).
    - Colapso de espacios múltiples a uno solo.
    """
    if not texto:
        return ""
    nfd = unicodedata.normalize("NFD", str(texto))
    sin_diacriticos = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    s = sin_diacriticos.lower().strip()
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"\s+y\s+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


_LINK_MD = r"\[([^\]]+)\]\(https?://[^\)]+\)"
_LINK = (
    r"(?:,\s*)?(?:desde|en|ingresando a|a través de|accediendo a|"
    r"haciendo clic en|haciendo click en|visita|visitando)?\s*:?\s*"
    r"(?:https?://|www\.)\S+"
)


def limpiar_mensaje_web(mensaje: str | None) -> str:
    """Saca links del texto de la campanita renglón por renglón, sin juntar renglones.
    Un renglón que quedó sin link y sin puntuación final termina con punto.
    Saca renglones vacíos."""
    if not mensaje:
        return ""
    salida: list[str] = []
    for linea in str(mensaje).replace("\r\n", "\n").split("\n"):
        sin_md = re.sub(_LINK_MD, r"\1", linea)
        limpia = re.sub(_LINK, "", sin_md, flags=re.IGNORECASE)
        limpia = re.sub(r"[ \t]+", " ", limpia).strip()
        if limpia != re.sub(r"[ \t]+", " ", sin_md).strip():
            limpia = re.sub(r"[:,]+$", "", limpia).strip()
            if limpia and not limpia.endswith((".", "!", "?")):
                limpia += "."
        if limpia:
            salida.append(limpia)
    return "\n".join(salida)

