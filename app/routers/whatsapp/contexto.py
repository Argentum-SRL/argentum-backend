"""
Contexto de procesamiento de mensajes de WhatsApp.
Define el dataclass ContextoMensaje que encapsula el estado compartido
entre las distintas etapas de procesamiento del webhook.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from sqlalchemy.orm import Session
from app.models.usuario import Usuario
from app.models.conversacion_wpp import ConversacionWpp


@dataclass
class ContextoMensaje:
    """Dataclass que transporta el estado y variables de un mensaje a lo largo del pipeline."""
    datos_mensaje: dict
    msg: dict
    wamid: str | None
    from_number: str
    msg_type: str
    t_inicio: float
    db: Session
    usuario: Usuario | None = None
    terminado: bool = False

    # Medios y texto de entrada
    mensaje_texto: str = ""
    transcripcion: str | None = None
    es_imagen: bool = False
    es_credito: bool = False
    es_lote: bool = False
    caption_imagen: str = ""
    extraccion: Any | None = None

    # Estados previos y conversaciones activas
    conv_activa: ConversacionWpp | None = None
    estado_previo: dict[str, Any] | None = None

    # Resultados de IA y normalización
    resultado_ia: dict[str, Any] | None = None
    aviso_montos_faltantes: str | None = None
    aviso_cambio_tema: str | None = None
    confianza_ia_raw: float = 1.0

    # Despacho y registro
    intent_detectado: str | None = None
    transaccion_id: Any | None = None
    nueva_conv: ConversacionWpp | None = None

    @property
    def es_medio(self) -> bool:
        """Indica si el tipo de mensaje corresponde a un medio multimedia (audio o imagen)."""
        return self.msg_type in ("audio", "image")
