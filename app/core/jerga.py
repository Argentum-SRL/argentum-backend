# -*- coding: utf-8 -*-
"""
Fuente única de verdad para la jerga argentina de montos y monedas.
"""
from __future__ import annotations

import re
from typing import Iterable

MULTIPLICADORES: dict[str, int] = {
    "mil": 1000,
    "k": 1000,
    "luca": 1000,
    "lucas": 1000,
    "palo": 1000000,
    "palos": 1000000,
    "melón": 1000000,
    "melon": 1000000,
    "melones": 1000000,
}

PALABRAS_PESOS: tuple[str, ...] = ("pesos", "peso", "mangos", "mango", "ars")

PALABRAS_DOLARES: tuple[str, ...] = (
    "dolares",
    "dólares",
    "dolar",
    "dólar",
    "usd",
    "u$s",
    "verdes",
    "verde",
)

NO_ES_MONTO: tuple[str, ...] = ("gamba", "gambas")


def _grupo_rx(palabras: Iterable[str]) -> str:
    """Arma un grupo regex no capturante ordenado de más largo a más corto."""
    ordenadas = sorted(palabras, key=lambda p: (-len(p), p))
    return "(?:" + "|".join(re.escape(p) for p in ordenadas) + ")"


def sufijo_multiplicador_rx() -> str:
    """Devuelve el grupo regex armado desde MULTIPLICADORES."""
    return _grupo_rx(MULTIPLICADORES.keys())


def monedas_pesos_rx() -> str:
    """Devuelve el grupo regex armado desde PALABRAS_PESOS."""
    return _grupo_rx(PALABRAS_PESOS)


def monedas_dolares_rx() -> str:
    """Devuelve el grupo regex armado desde PALABRAS_DOLARES."""
    return _grupo_rx(PALABRAS_DOLARES)


def multiplicador_de(palabra: str | None) -> int | None:
    """Devuelve el multiplicador numérico para una palabra de jerga, o None."""
    if not palabra:
        return None
    return MULTIPLICADORES.get(palabra.strip().lower())
