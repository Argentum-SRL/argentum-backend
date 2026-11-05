# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any
from uuid import UUID
from pydantic import BaseModel


class ConsejoActualItem(BaseModel):
    id: UUID
    tipo: str
    texto: str
    accion: str | None = None
    accion_texto: str | None = None
    accion_datos: dict[str, Any] | None = None


class ConsejoActualResponse(BaseModel):
    consejo: ConsejoActualItem | None = None


class RespuestaConsejoRequest(BaseModel):
    respuesta: str


class RespuestaConsejoResponse(BaseModel):
    ok: bool = True
