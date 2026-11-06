"""
app/core/limiter.py — Instancia central de Rate Limiter (SlowAPI) para Argentum respaldada en Postgres.
"""

from slowapi import Limiter
from app.core.ip_cliente import clave_limite
from app.services.rate_limit_service import PostgresStorage

_storage = PostgresStorage()
limiter = Limiter(key_func=clave_limite)
limiter._storage = _storage
limiter._limiter._storage = _storage
