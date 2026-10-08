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


def limpiar_mensaje_web(mensaje: str | None) -> str:
    """
    Remueve enlaces/URLs y frases introductorias a enlaces de los mensajes
    para la versión web (centro de notificaciones en la app).
    """
    if not mensaje:
        return ""
    texto = str(mensaje)
    # Enlaces en formato markdown [texto](url) -> texto
    texto = re.sub(r"\[([^\]]+)\]\(https?://[^\)]+\)", r"\1", texto)
    # Remover patrones como: ", desde https://...", " desde https://...", "https://...", "www...."
    texto = re.sub(
        r"(?:,\s*)?(?:desde|en|ingresando a|a través de|accediendo a|haciendo clic en|haciendo click en|visita|visitando)?\s*:?\s*(?:https?://|www\.)\S+",
        "",
        texto,
        flags=re.IGNORECASE,
    ).strip()
    # Limpiar dos puntos o comas que hayan quedado huérfanas al final
    texto = re.sub(r"[:,]+$", "", texto).strip()
    # Limpiar espacios dobles
    texto = re.sub(r"\s+", " ", texto)
    # Asegurar puntuación final si hay contenido y no termina con puntuación
    if texto and not texto.endswith((".", "!", "?")):
        texto += "."
    return texto

