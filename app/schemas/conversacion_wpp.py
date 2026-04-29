from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.conversacion_wpp import TipoMensajeWpp


class ConversacionWppBase(BaseModel):
    usuario_id: UUID
    mensaje_usuario: str
    tipo_mensaje: TipoMensajeWpp = TipoMensajeWpp.TEXTO
    transcripcion: str | None = None
    mensaje_bot: str
    intent_detectado: str | None = None
    entidades: dict[str, Any] | None = None
    accion_ejecutada: str | None = None
    confianza: Decimal | None = None
    slot_filling_activo: bool = False
    slot_filling_estado: dict[str, Any] | None = None


class ConversacionWppCreate(ConversacionWppBase):
    pass


class ConversacionWppUpdate(BaseModel):
    mensaje_usuario: str | None = None
    tipo_mensaje: TipoMensajeWpp | None = None
    transcripcion: str | None = None
    mensaje_bot: str | None = None
    intent_detectado: str | None = None
    entidades: dict[str, Any] | None = None
    accion_ejecutada: str | None = None
    confianza: Decimal | None = None
    slot_filling_activo: bool | None = None
    slot_filling_estado: dict[str, Any] | None = None


class ConversacionWppRead(ConversacionWppBase):
    id: UUID
    fecha: datetime

    model_config = ConfigDict(from_attributes=True)