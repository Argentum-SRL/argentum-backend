# -*- coding: utf-8 -*-
"""
app/core/ip_cliente.py — Determinación de IP real del cliente y generación de claves anónimas para rate limiting.

Razón: Railway pisa el encabezado X-Real-IP en su borde perimetral con la dirección IP real del cliente.
En el entorno local (PC) y en las pruebas automatizadas no existe dicho encabezado y se recurre a la conexión
directa (request.client.host).
Para cumplir con la política de privacidad y no almacenar IPs en claro, clave_limite genera un resumen
HMAC truncado a 32 caracteres hexadecimales.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from starlette.requests import Request

from app.core.config import settings


def ip_cliente(request: Request | Any) -> str | None:
    """
    Obtiene la dirección IP real del cliente.
    Prioriza el encabezado 'x-real-ip' inyectado por Railway en su borde perimetral.
    Si no existe o está vacío, recurre a request.client.host (desarrollo local / tests).
    Si request.client es None, retorna None.
    """
    valor = request.headers.get("x-real-ip", "") if hasattr(request, "headers") else ""
    primero = valor.split(",")[0].strip() if valor else ""
    if primero:
        return primero

    if hasattr(request, "client") and request.client:
        return getattr(request.client, "host", None)

    return None


def clave_limite(request: Request | Any) -> str:
    """
    Genera una clave anónima para rate limiting mediante HMAC-SHA256 truncado a 32 caracteres.
    Garantiza que la tabla rate_limits nunca guarde direcciones IP en texto plano, cumpliendo
    con la política de privacidad de Argentum.
    """
    ip = ip_cliente(request) or "sin_ip"
    msg = ("ip:" + ip).encode("utf-8")
    key = settings.SECRET_KEY.encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]

