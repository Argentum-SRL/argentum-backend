from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.uso_ia import UsoIA
from app.models.usuario import Usuario
from app.services.alerta_service import enviar_alerta_admin
from app.services.rate_limit_service import verificar_rate_limit
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)

# Precios estimados: revisar en la página de precios de OpenAI
PRECIOS_IA: dict[str, dict[str, Decimal]] = {
    "gpt-4o-mini": {
        "entrada": Decimal("0.15"),
        "entrada_cache": Decimal("0.075"),
        "salida": Decimal("0.60"),
    },
    "gpt-4o": {
        "entrada": Decimal("2.50"),
        "entrada_cache": Decimal("1.25"),
        "salida": Decimal("10.00"),
    },
    "whisper-1": {
        "precio_por_minuto": Decimal("0.006"),
    },
}

TEXTO_TOPE_MENSAJES_USUARIO = (
    "Por hoy llegaste al máximo de mensajes. Mañana seguimos; mientras tanto podés cargar tus movimientos desde la web."
)
TEXTO_TOPE_ARCHIVOS_USUARIO = (
    "Por hoy llegaste al máximo de fotos y archivos. Mañana los leo; mientras tanto escribime el movimiento, por ejemplo: gasté 2500 en el súper."
)
TEXTO_GLOBAL_SIN_ARCHIVOS = (
    "Ahora no puedo leer fotos ni archivos. Escribime el movimiento, por ejemplo: gasté 2500 en el súper."
)
TEXTO_GLOBAL_SIN_IA = (
    "Por hoy no puedo responder más por acá. Cargá tus movimientos desde la web; mañana vuelvo a andar."
)


def calcular_costo_ia(
    modelo: str,
    tokens_entrada: int = 0,
    tokens_entrada_cache: int = 0,
    tokens_salida: int = 0,
    segundos_audio: int = 0,
) -> Decimal:
    """
    Calcula el costo estimado en USD de una llamada a OpenAI.
    Aplica redondeo a 6 decimales.
    """
    if modelo == "whisper-1":
        precio_minuto = PRECIOS_IA["whisper-1"]["precio_por_minuto"]
        costo = Decimal(segundos_audio) * (precio_minuto / Decimal("60"))
        return round(costo, 6)

    if modelo.startswith("gpt-4o-mini"):
        precios = PRECIOS_IA["gpt-4o-mini"]
    elif modelo.startswith("gpt-4o"):
        precios = PRECIOS_IA["gpt-4o"]
    else:
        logger.warning(
            "Modelo desconocido '%s', usando precios de gpt-4o para estimación de costo",
            modelo,
        )
        precios = PRECIOS_IA["gpt-4o"]

    sin_cache = max(0, tokens_entrada - tokens_entrada_cache)
    costo_tokens = (
        Decimal(sin_cache) * precios["entrada"]
        + Decimal(tokens_entrada_cache) * precios["entrada_cache"]
        + Decimal(tokens_salida) * precios["salida"]
    ) / Decimal("1000000")
    return round(costo_tokens, 6)


def _extraer_tokens_de_respuesta(respuesta: Any) -> tuple[int, int, int]:
    """Extrae tokens_entrada, tokens_entrada_cache y tokens_salida de un objeto o dict de respuesta OpenAI."""
    if respuesta is None:
        return 0, 0, 0

    usage = getattr(respuesta, "usage", None)
    if isinstance(respuesta, dict) and "usage" in respuesta:
        usage = respuesta["usage"]

    if usage is None:
        return 0, 0, 0

    if isinstance(usage, dict):
        t_in = usage.get("prompt_tokens") or 0
        t_out = usage.get("completion_tokens") or 0
        detalles = usage.get("prompt_tokens_details") or {}
        if isinstance(detalles, dict):
            t_cache = detalles.get("cached_tokens") or 0
        else:
            t_cache = getattr(detalles, "cached_tokens", 0) or 0
        return int(t_in), int(t_cache), int(t_out)

    t_in = getattr(usage, "prompt_tokens", 0) or 0
    t_out = getattr(usage, "completion_tokens", 0) or 0
    detalles = getattr(usage, "prompt_tokens_details", None)
    t_cache = 0
    if detalles is not None:
        if isinstance(detalles, dict):
            t_cache = detalles.get("cached_tokens") or 0
        else:
            t_cache = getattr(detalles, "cached_tokens", 0) or 0
    return int(t_in), int(t_cache), int(t_out)


def registrar_uso_ia(
    usuario_id: UUID | None,
    funcion: str,
    modelo: str,
    respuesta: Any = None,
    segundos_audio: int = 0,
) -> None:
    """
    Registra el consumo de una llamada exitosa a OpenAI en la tabla uso_ia.
    Usa su propia sesión SessionLocal() independiente de la transacción del flujo y hace commit.
    Nunca propaga excepciones.
    """
    try:
        t_in, t_cache, t_out = _extraer_tokens_de_respuesta(respuesta)
        costo_usd = calcular_costo_ia(
            modelo=modelo,
            tokens_entrada=t_in,
            tokens_entrada_cache=t_cache,
            tokens_salida=t_out,
            segundos_audio=segundos_audio,
        )

        db = SessionLocal()
        try:
            registro = UsoIA(
                usuario_id=usuario_id,
                funcion=funcion,
                modelo=modelo,
                tokens_entrada=t_in,
                tokens_entrada_cache=t_cache,
                tokens_salida=t_out,
                segundos_audio=segundos_audio,
                costo_usd=costo_usd,
            )
            db.add(registro)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception:
        logger.exception("Error al registrar uso de IA en uso_ia")


def _fmt(val: float | Decimal) -> str:
    return f"{float(val):.2f}"


def _enviar_alerta_global_si_corresponde(
    nivel: str,
    gasto_dia: Decimal,
    presupuesto: Decimal,
    asunto: str,
) -> None:
    """Envía un email al admin notificando el nivel de gasto diario si no fue enviado hoy."""
    fecha_iso = hoy_argentina().isoformat()
    permitido, _, _ = verificar_rate_limit(
        accion=f"alerta_ia_{nivel}",
        identificador=fecha_iso,
        max_intentos=1,
        ventana_segundos=86400,
    )
    if permitido:
        cuerpo = (
            f"Gasto estimado del día: {_fmt(gasto_dia)} USD\n"
            f"Presupuesto diario: {_fmt(presupuesto)} USD\n"
            "Monto estimado por Argentum, no facturado por OpenAI"
        )
        enviar_alerta_admin(asunto=asunto, cuerpo=cuerpo)


def _enviar_alerta_tope_usuario_si_corresponde(
    usuario_id: UUID | str,
    cual_tope: str,
) -> None:
    """Envía un email al admin notificando que un usuario llegó a su tope diario (máximo 1 por día por usuario)."""
    fecha_iso = hoy_argentina().isoformat()
    permitido, _, _ = verificar_rate_limit(
        accion=f"alerta_ia_tope_usuario_{cual_tope}",
        identificador=f"{usuario_id}_{fecha_iso}",
        max_intentos=1,
        ventana_segundos=86400,
    )
    if permitido:
        asunto = "Argentum: un usuario llegó al tope diario de la IA"
        cuerpo = (
            f"El usuario {usuario_id} llegó al tope diario de {cual_tope}.\n"
            "Monto estimado por Argentum, no facturado por OpenAI"
        )
        enviar_alerta_admin(asunto=asunto, cuerpo=cuerpo)


def verificar_cupo_ia(
    db: Session,
    usuario: Usuario | None,
    tipo: str,
) -> tuple[bool, str | None]:
    """
    Verifica los topes por usuario y el presupuesto global diario antes de interactuar con la IA.
    tipo: 'mensaje' | 'archivo' | 'audio'.
    Retorna: (permitido: bool, texto_mensaje_al_usuario_o_none).
    Nunca propaga excepciones: si algo falla, logger.exception y devuelve (True, None).
    """
    try:
        # 1. Presupuesto global diario
        hoy = hoy_argentina()
        inicio_dia_ar = datetime(hoy.year, hoy.month, hoy.day, 0, 0, 0, tzinfo=TZ_ARGENTINA)

        gasto_dia = db.scalar(
            select(func.coalesce(func.sum(UsoIA.costo_usd), 0)).where(UsoIA.fecha >= inicio_dia_ar)
        )
        if gasto_dia is None:
            gasto_dia = Decimal("0")
        else:
            gasto_dia = Decimal(str(gasto_dia))

        presupuesto = Decimal(str(getattr(settings, "IA_PRESUPUESTO_DIARIO_USD", 1.0)))

        # Evaluación de niveles de gasto global y alertas por orden de severidad
        if gasto_dia >= presupuesto * Decimal("2.0"):
            _enviar_alerta_global_si_corresponde(
                "sin_ia",
                gasto_dia,
                presupuesto,
                "Argentum: se pausó la IA por el presupuesto del día",
            )
            return False, TEXTO_GLOBAL_SIN_IA
        elif gasto_dia >= presupuesto * Decimal("1.0"):
            _enviar_alerta_global_si_corresponde(
                "sin_archivos",
                gasto_dia,
                presupuesto,
                "Argentum: se pausaron fotos y PDF por el presupuesto de la IA",
            )
            if tipo == "archivo":
                return False, TEXTO_GLOBAL_SIN_ARCHIVOS
        elif gasto_dia >= presupuesto * Decimal("0.5"):
            _enviar_alerta_global_si_corresponde(
                "aviso",
                gasto_dia,
                presupuesto,
                "Argentum: la IA ya gastó la mitad del presupuesto de hoy",
            )

        # 2. Topes por usuario (WhatsApp)
        if usuario is not None and getattr(usuario, "id", None) is not None:
            if tipo == "mensaje":
                max_mensajes = getattr(settings, "IA_MAX_MENSAJES_DIA_USUARIO", 150)
                permitido_msg, _, _ = verificar_rate_limit(
                    accion="mensajes_ia_dia",
                    identificador=str(usuario.id),
                    max_intentos=max_mensajes,
                    ventana_segundos=86400,
                )
                if not permitido_msg:
                    _enviar_alerta_tope_usuario_si_corresponde(usuario.id, "mensajes")
                    return False, TEXTO_TOPE_MENSAJES_USUARIO

            elif tipo == "archivo":
                max_archivos = getattr(settings, "IA_MAX_ARCHIVOS_DIA_USUARIO", 20)
                permitido_arch, _, _ = verificar_rate_limit(
                    accion="archivos_ia_dia",
                    identificador=str(usuario.id),
                    max_intentos=max_archivos,
                    ventana_segundos=86400,
                )
                if not permitido_arch:
                    _enviar_alerta_tope_usuario_si_corresponde(usuario.id, "archivos")
                    return False, TEXTO_TOPE_ARCHIVOS_USUARIO

        return True, None
    except Exception:
        logger.exception("Error al verificar cupo de IA")
        return True, None


def purgar_uso_ia_antiguo(db: Session) -> int:
    """
    Elimina registros de uso_ia con más de 400 días de antigüedad.
    Retorna la cantidad de filas eliminadas.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import delete
    limite = datetime.now(timezone.utc) - timedelta(days=400)
    borrados = db.execute(
        delete(UsoIA).where(UsoIA.fecha < limite).execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    logger.info(
        "Purga de uso_ia ejecutada: %d filas eliminadas (anteriores a %s).",
        borrados,
        limite.isoformat(),
    )
    return borrados
