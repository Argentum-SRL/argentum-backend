from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

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

    @field_validator("mensaje_usuario")
    @classmethod
    def validar_mensaje_usuario(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El mensaje del usuario no puede estar vacío")
        return v.strip()

    @field_validator("mensaje_bot")
    @classmethod
    def validar_mensaje_bot(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("El mensaje del asistente no puede estar vacío")
        return v.strip()

    @field_validator("confianza")
    @classmethod
    def validar_confianza(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v < 0 or v > 1:
                raise ValueError("El nivel de confianza debe estar entre 0.0 y 1.0")
        return v


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

    @field_validator("confianza")
    @classmethod
    def validar_confianza_update(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v < 0 or v > 1:
                raise ValueError("El nivel de confianza debe estar entre 0.0 y 1.0")
        return v


class ConversacionWppRead(ConversacionWppBase):
    id: UUID
    fecha: datetime

    model_config = ConfigDict(from_attributes=True)