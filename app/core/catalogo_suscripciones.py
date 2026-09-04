from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


_CATALOGO_PATH = Path(__file__).resolve().parent / "catalogo_suscripciones.json"


def cargar_catalogo() -> list[dict[str, Any]]:
    """Carga los 28 servicios del catálogo de suscripciones compartido."""
    with open(_CATALOGO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


CATALOGO_SERVICIOS: list[dict[str, Any]] = cargar_catalogo()


def buscar_servicio_por_texto(texto: str) -> Optional[dict[str, Any]]:
    """
    Busca un servicio en el catálogo por coincidencia exacta o por variantes.
    Listo para ser utilizado por el procesador de WhatsApp en la siguiente etapa.
    """
    if not texto:
        return None
    texto_norm = texto.strip().lower()
    for serv in CATALOGO_SERVICIOS:
        if serv["nombre"].lower() == texto_norm or serv["id"].lower() == texto_norm:
            return serv
        for v in serv.get("variantes", []):
            if v.lower() == texto_norm:
                return serv
    return None
