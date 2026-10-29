# -*- coding: utf-8 -*-
"""
pregunta_saldo.py - Esquema de respuesta para la pregunta de saldo en el dashboard.
"""
from __future__ import annotations

from pydantic import BaseModel
from app.schemas.billetera import BilleteraRead


class PreguntaSaldoResponse(BaseModel):
    billetera: BilleteraRead | None = None

