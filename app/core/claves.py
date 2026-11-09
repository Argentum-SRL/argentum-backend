# -*- coding: utf-8 -*-
"""
app/core/claves.py — Manejo de claves criptográficas y separación de propósitos (fase_j2_auth).

SECRET_KEY queda exclusivamente para la firma y verificación de tokens JWT.
HMAC_SECRET_KEY se utiliza para resúmenes HMAC (refresh tokens, códigos de verificación/recuperación,
límite de pedidos por IP y número de WhatsApp).
"""
from __future__ import annotations

import logging
from app.core.config import settings

logger = logging.getLogger(__name__)


def clave_hmac() -> bytes:
    return (settings.HMAC_SECRET_KEY or settings.SECRET_KEY).encode("utf-8")


def claves_hmac_verificacion() -> list[bytes]:
    claves = [clave_hmac()]
    if settings.HMAC_SECRET_KEY and settings.HMAC_SECRET_KEY != settings.SECRET_KEY:
        # TRANSICIÓN (fase_j2_auth, 09/11/2026): acepta lo guardado con SECRET_KEY antes de cargar HMAC_SECRET_KEY (refresh tokens de hasta 30 días, códigos de 15 minutos). Se saca después del 10/12/2026.
        claves.append(settings.SECRET_KEY.encode("utf-8"))
    return claves


def avisar_si_falta_clave_hmac() -> bool:
    if not settings.ES_MODO_PC and not settings.HMAC_SECRET_KEY:
        logger.warning("Falta HMAC_SECRET_KEY: los resúmenes HMAC usan SECRET_KEY.")
        return True
    return False

