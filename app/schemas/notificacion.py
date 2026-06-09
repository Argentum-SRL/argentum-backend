from __future__ import annotations

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from app.models.notificacion import TipoNotificacion, NivelNotificacion


class NotificacionBase(BaseModel):
    usuario_id: UUID
    tipo: TipoNotificacion
    nivel: NivelNotificacion
    mensaje: str
    leida: bool = False
    archivada: bool = False
    canal_web: bool = True
    canal_whatsapp: bool = False
    canal_email: bool = False
    enviada_whatsapp: bool = False
    enviada_email: bool = False
    grupo_agrupacion: str | None = None
    entidad_tipo: str | None = None
    entidad_id: UUID | None = None
    deep_link: str | None = None
    silenciada_hasta: datetime | None = None


class NotificacionCreate(NotificacionBase):
    pass


class NotificacionUpdate(BaseModel):
    leida: bool | None = None
    archivada: bool | None = None
    silenciada_hasta: datetime | None = None


class NotificacionRead(NotificacionBase):
    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)