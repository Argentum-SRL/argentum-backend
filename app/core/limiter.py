"""
app/core/limiter.py — Instancia central de Rate Limiter (SlowAPI) para Argentum respaldada en Postgres.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from app.services.rate_limit_service import PostgresStorage

_storage = PostgresStorage()
limiter = Limiter(key_func=get_remote_address)
limiter._storage = _storage
limiter._limiter._storage = _storage
