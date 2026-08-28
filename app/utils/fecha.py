import logging
from datetime import date, datetime, timezone, timedelta
import zoneinfo

logger = logging.getLogger("app.utils.fecha")

try:
    TZ_ARGENTINA = zoneinfo.ZoneInfo("America/Argentina/Buenos_Aires")
except Exception as e:
    logger.warning(
        "No se pudo cargar la zona horaria IANA 'America/Argentina/Buenos_Aires'. "
        "Usando fallback a offset fijo UTC-3. Instalar 'tzdata' para soporte completo.",
        exc_info=True
    )
    TZ_ARGENTINA = timezone(timedelta(hours=-3))


def ahora_argentina() -> datetime:
    """Retorna la fecha y hora actual en zona horaria de Argentina (America/Argentina/Buenos_Aires, UTC-3)."""
    return datetime.now(TZ_ARGENTINA)


def hoy_argentina() -> date:
    """Retorna la fecha actual (date) en zona horaria de Argentina."""
    return ahora_argentina().date()
