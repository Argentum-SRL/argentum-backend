"""
app/routers/whatsapp/constantes.py — Constantes compartidas para el flujo de WhatsApp en Argentum.
"""
from decimal import Decimal
import structlog

logger = structlog.get_logger("whatsapp")

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


def texto_cupo_wpp(texto_cupo: str, canal: str = "mensaje") -> str:
    """Convierte los textos de tope de uso de IA al formato de WhatsApp (D12)."""
    t = (texto_cupo or "").lower()
    if "este mes" in t:
        return (
            "Este mes ya no puedo responder por acá. "
            "Cargá tus movimientos en miargentum.com"
        )
    if canal == "archivo" or "fotos" in t or "archivos" in t:
        return (
            "Por hoy no leo más fotos ni archivos. "
            "Escribime el gasto, por ejemplo: _2500 súper_."
        )
    return (
        "Por hoy llegué al máximo de mensajes. "
        "Mañana seguimos; mientras, cargá en miargentum.com"
    )
