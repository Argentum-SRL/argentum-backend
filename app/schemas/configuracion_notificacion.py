from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.notificacion import TipoNotificacion


class ConfiguracionNotificacionBase(BaseModel):
    usuario_id: UUID
    tipo: TipoNotificacion
    canal_wpp: bool = True
    canal_app: bool = True
    anticipacion_dias: int | None = None


class ConfiguracionNotificacionCreate(ConfiguracionNotificacionBase):
    pass


class ConfiguracionNotificacionUpdate(BaseModel):
    canal_wpp: bool | None = None
    canal_app: bool | None = None
    anticipacion_dias: int | None = None


class ConfiguracionNotificacionRead(ConfiguracionNotificacionBase):
    id: UUID

    model_config = ConfigDict(from_attributes=True)