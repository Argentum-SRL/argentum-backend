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
