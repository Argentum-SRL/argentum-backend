# -*- coding: utf-8 -*-
"""
Centraliza la detección de modelos con razonamiento y el armado de
parámetros para cada llamada a OpenAI.
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings

modelos_por_minuto: frozenset[str] = frozenset(
    {
        "whisper-1",
        "gpt-transcribe",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
    }
)


def es_modelo_razonamiento(modelo: str | None) -> bool:
    """Devuelve True si el modelo usa reasoning_effort y max_completion_tokens."""
    nombre = (modelo or "").strip().lower()
    return nombre.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4"))


def parametros_chat(
    modelo: str,
    temperatura: float,
    max_salida: int | None,
) -> dict[str, Any]:
    """Arma los kwargs de temperatura, tokens y razonamiento para chat."""
    if not es_modelo_razonamiento(modelo):
        params: dict[str, Any] = {"temperature": temperatura}
        if max_salida is not None:
            params["max_tokens"] = max_salida
        return params

    razonamiento = (settings.OPENAI_RAZONAMIENTO or "none").strip().lower()
    if max_salida is None:
        max_comp = 6000
    elif razonamiento == "none":
        max_comp = max_salida
    else:
        max_comp = max_salida + 4000

    params = {
        "reasoning_effort": razonamiento,
        "max_completion_tokens": max_comp,
    }
    if razonamiento == "none":
        params["temperature"] = temperatura
    return params
