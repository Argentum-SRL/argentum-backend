"""
app/routers/whatsapp/constantes.py — Constantes compartidas para el flujo de WhatsApp en Argentum.
"""
from decimal import Decimal

PREFIJOS_CORRECCION = [
    r"^no,?\s+fue\s+en\s+",
    r"^no,?\s+fue\s+con\s+",
    r"^no,?\s+era\s+en\s+",
    r"^no,?\s+era\s+con\s+",
    r"^no,?\s+en\s+",
    r"^no,?\s+con\s+",
    r"^no,?\s+",
    r"^fue\s+en\s+",
    r"^fue\s+con\s+",
    r"^era\s+en\s+",
    r"^era\s+con\s+",
    r"^cambia\s+a\s+",
    r"^cambiala\s+a\s+",
    r"^pasalo\s+a\s+",
    r"^ponele\s+",
    r"^pone\s+",
    r"^mejor\s+",
    r"^en\s+",
    r"^con\s+",
    r"^desde\s+",
    r"^a\s+",
]

MESES_ES_GEN = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]

FACTOR_MIN_COTIZACION_DOLAR = Decimal("0.40")
FACTOR_MAX_COTIZACION_DOLAR = Decimal("2.50")
