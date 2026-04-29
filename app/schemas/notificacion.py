from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.notificacion import TipoNotificacion


class NotificacionBase(BaseModel):
    usuario_id: UUID
    tipo: TipoNotificacion
    titulo: str
    mensaje: str
    leida: bool = False
    modulo_ref: str | None = None


class NotificacionCreate(NotificacionBase):
    pass


class NotificacionUpdate(BaseModel):
    tipo: TipoNotificacion | None = None
    titulo: str | None = None
    mensaje: str | None = None
    leida: bool | None = None
    modulo_ref: str | None = None


class NotificacionRead(NotificacionBase):
    id: UUID
    fecha_creacion: datetime

    model_config = ConfigDict(from_attributes=True)