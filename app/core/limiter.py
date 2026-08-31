"""
app/core/limiter.py — Instancia central de Rate Limiter (SlowAPI) para Argentum.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
