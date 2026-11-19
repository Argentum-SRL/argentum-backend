# -*- coding: utf-8 -*-
"""
app/routers/whatsapp/handlers_conveniencia.py

Handler determinístico para consultas de "Cuotas vs contado" por WhatsApp (Bloque T2).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import re
import structlog

from app.core.jerga import sufijo_multiplicador_rx
from app.core.reglas_texto_wpp import ejemplo_wpp, partir_lineas_wpp
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.conveniencia_flujo import (
    INTENT_ORIGEN,
    armar_estado_pregunta,
    buscar_pregunta_pendiente,
    leer_respuesta_pendiente,
)
from app.routers.whatsapp.parsers import _parsear_monto_argentino
from app.services import whatsapp_service
from app.services.uso_service import registrar_evento
from app.services.conveniencia_service import (
    comparar_cuotas_contado,
    textos_conveniencia,
)
from app.services.inflacion_esperada_service import inflacion_esperada
from app.utils.fecha import hoy_argentina
from app.utils.texto import normalizar_texto

logger = structlog.get_logger(__name__)

TRIGGERS_CONVENIENCIA = (
    "conviene",
    "contado o",
    "o contado",
    "cuotas o",
)

_MONTO_RX = rf"(?:\$\s*)?\d+(?:[\.,]\d+)*(?:\s*{sufijo_multiplicador_rx()})?"
_PATRON_CONTADO_DESPUES = re.compile(
    rf"({_MONTO_RX})\s+(?:de\s+contado|contado|en\s+efectivo)\b"
)
_PATRON_CONTADO_ANTES = re.compile(
    rf"\b(?:contado|en\s+efectivo)\s+(?:de\s+|a\s+)?({_MONTO_RX})"
)
_PATRON_CONTADO_CONTRA = re.compile(
    rf"\b(?:contra|vs\.?|o)\s+(?:un\s+)?(?:contado\s+(?:de\s+)?)?"
    rf"({_MONTO_RX})(?!\s*(?:cuotas?|pagos?|%))"
)
_PATRON_CUOTAS_DE = re.compile(
    rf"\b(\d{{1,3}})\s+(?:cuotas?\s+)?(?:sin\s+interes\s+)?de\s+({_MONTO_RX})"
)
_PATRON_CUOTAS_SIN_INTERES = re.compile(
    r"\b(\d{1,3})\s+cuotas?\s+sin\s+interes\b"
)
_PATRON_CUOTAS_SOLO = re.compile(r"\b(\d{1,3})\s+cuotas?\b")


def analizar_consulta_conveniencia(texto: str) -> dict | None:
    """
    Detecta consultas de 'Cuotas vs contado' y extrae precio de contado,
    cantidad de cuotas y valor de cuota (o sin interés).
    """
    if not texto:
        return None

    norm = normalizar_texto(texto)
    if not norm:
        return None

    if not any(t in norm for t in TRIGGERS_CONVENIENCIA):
        return None

    m_contado = (
        _PATRON_CONTADO_DESPUES.search(norm)
        or _PATRON_CONTADO_ANTES.search(norm)
        or _PATRON_CONTADO_CONTRA.search(norm)
    )
    if not m_contado:
        return None

    precio_contado = _parsear_monto_argentino(m_contado.group(1))
    if precio_contado is None or precio_contado <= 0:
        return None

    # Enmascara el tramo de contado para no confundir montos al leer cuotas
    resto = (
        norm[:m_contado.start()]
        + (" " * (m_contado.end() - m_contado.start()))
        + norm[m_contado.end():]
    )

    cantidad: int | None = None
    valor_cuota: Decimal | None = None
    sin_interes = False
    falta: str | None = None

    m_de = _PATRON_CUOTAS_DE.search(resto)
    if m_de:
        cantidad = int(m_de.group(1))
        valor_cuota = _parsear_monto_argentino(m_de.group(2))
        sin_interes = "sin interes" in resto
        if valor_cuota is None or valor_cuota <= 0:
            return None
    else:
        m_si = _PATRON_CUOTAS_SIN_INTERES.search(resto)
        if m_si:
            cantidad = int(m_si.group(1))
            sin_interes = True
            if 1 <= cantidad <= 120:
                valor_cuota = (precio_contado / Decimal(cantidad)).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
        else:
            m_solo = _PATRON_CUOTAS_SOLO.search(resto)
            if m_solo:
                cantidad = int(m_solo.group(1))
                falta = "cuota"

    if cantidad is None or cantidad < 1 or cantidad > 120:
        return None

    return {
        "precio_contado": precio_contado,
        "cantidad_cuotas": cantidad,
        "valor_cuota": valor_cuota,
        "sin_interes": sin_interes,
        "falta": falta,
    }


def _armar_mensaje_pregunta_cuota() -> str:
    """Arma el texto de pregunta pendiente de valor de cuota."""
    ej = ejemplo_wpp("12 cuotas de 50 mil")
    return f"Decime cuánto es cada cuota o si son sin interés. Por ejemplo: {ej}"


def _responder_conveniencia(ctx: ContextoMensaje, consulta: dict) -> bool:
    """Responde la consulta de cuotas vs contado (o pregunta la cuota si falta)."""
    db = ctx.db
    usuario = ctx.usuario
    estado_pregunta: dict | None = None
    calculado = False

    if consulta.get("falta") == "cuota":
        msg_resp = _armar_mensaje_pregunta_cuota()
        estado_pregunta = armar_estado_pregunta(consulta)
    else:
        try:
            cant = int(consulta["cantidad_cuotas"])
            inf_esp = inflacion_esperada(db, hoy_argentina(), cant)
            if inf_esp is None or not inf_esp.tasas:
                msg_resp = (
                    "No tengo datos de inflación ahora. Probá en miargentum.com"
                )
            else:
                comp = comparar_cuotas_contado(
                    precio_contado=consulta["precio_contado"],
                    cantidad=cant,
                    valor_cuota=consulta["valor_cuota"],
                    inflacion_mensual=inf_esp.tasas,
                )
                cuota_r = Decimal(str(consulta["valor_cuota"])).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                txts = textos_conveniencia(
                    resultado=comp["resultado"],
                    cantidad=cant,
                    valor_cuota=cuota_r,
                    total=comp["total"],
                    extra=comp["extra"],
                    extra_pct=comp["extra_pct"],
                    vp=comp["vp"],
                    fuente_texto=inf_esp.fuente_texto,
                )
                crudo = f"{txts['titulo']}\n{txts['linea_2']}\n{txts['linea_3']}"
                msg_resp = partir_lineas_wpp(crudo)
                calculado = True
        except Exception as exc:
            logger.warning(
                "Error calculando cuotas vs contado",
                error=str(exc),
                exc_info=True,
            )
            msg_resp = (
                "No pude calcularlo ahora. Probá de nuevo en unos minutos."
            )

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=ctx.wamid,
        mensaje_usuario=ctx.mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_resp,
        intent_detectado=INTENT_ORIGEN,
        entidades={
            "precio_contado": float(consulta["precio_contado"]),
            "cantidad_cuotas": int(consulta["cantidad_cuotas"]),
            "valor_cuota": (
                float(consulta["valor_cuota"])
                if consulta.get("valor_cuota") is not None
                else None
            ),
        },
        accion_ejecutada="consulta",
        confianza=Decimal("1.000"),
        slot_filling_activo=estado_pregunta is not None,
        slot_filling_estado=estado_pregunta,
    )
    db.add(nueva_conv)
    if calculado:
        registrar_evento(db, usuario.id, "cuotas_contado_wpp", commit=False)
    db.commit()
    whatsapp_service.enviar_whatsapp(ctx.from_number, msg_resp)
    return True


def manejar_consulta_conveniencia(ctx: ContextoMensaje) -> bool:
    """Handler determinístico de 'Cuotas vs contado' antes de permitirse."""
    if not ctx.usuario:
        return False
    consulta = analizar_consulta_conveniencia(ctx.mensaje_texto)
    if consulta is None:
        return False
    return _responder_conveniencia(ctx, consulta)


def manejar_respuesta_pendiente_conveniencia(ctx: ContextoMensaje) -> bool:
    """
    Si hay una pregunta pendiente de 'Cuotas vs contado' y el mensaje trae
    el valor de cuota o 'sin interés', completa y responde; si no, cierra y sigue.
    """
    if not ctx.usuario:
        return False
    pregunta = buscar_pregunta_pendiente(ctx.db, ctx.usuario.id)
    if pregunta is None:
        return False
    consulta = leer_respuesta_pendiente(
        ctx.mensaje_texto, pregunta.slot_filling_estado
    )
    pregunta.slot_filling_activo = False
    if consulta is None:
        ctx.db.commit()
        return False
    return _responder_conveniencia(ctx, consulta)
