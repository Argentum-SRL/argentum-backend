from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from app.utils.texto import normalizar_texto


_CATALOGO_PATH = Path(__file__).resolve().parent / "catalogo_suscripciones.json"


def cargar_catalogo() -> list[dict[str, Any]]:
    """Carga los 28 servicios del catálogo de suscripciones compartido."""
    with open(_CATALOGO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


CATALOGO_SERVICIOS: list[dict[str, Any]] = cargar_catalogo()


def buscar_servicio_por_texto(texto: str) -> Optional[dict[str, Any]]:
    """
    Busca un servicio en el catálogo por coincidencia exacta o por variantes,
    utilizando normalizar_texto.
    """
    if not texto:
        return None
    texto_norm = normalizar_texto(texto)
    for serv in CATALOGO_SERVICIOS:
        if normalizar_texto(serv["nombre"]) == texto_norm or normalizar_texto(serv["id"]) == texto_norm:
            return serv
        for v in serv.get("variantes", []):
            if normalizar_texto(v) == texto_norm:
                return serv
    return None


def identificar_servicio_en_texto(texto: str) -> Optional[dict[str, Any]]:
    """
    Busca si en el texto se menciona algún servicio del catálogo.
    Prueba primero match exacto y luego búsqueda por delimitadores de palabra,
    ordenando variantes de mayor a menor longitud.
    """
    if not texto:
        return None

    direct = buscar_servicio_por_texto(texto)
    if direct:
        return direct

    texto_norm = normalizar_texto(texto)
    candidatos = []
    for serv in CATALOGO_SERVICIOS:
        variantes = [serv["nombre"], serv["id"]] + serv.get("variantes", [])
        for v in variantes:
            v_norm = normalizar_texto(v)
            if len(v_norm) >= 3:
                candidatos.append((len(v_norm), v_norm, serv))

    candidatos.sort(key=lambda x: x[0], reverse=True)

    for _, v_norm, serv in candidatos:
        patron = r"(?:^|\s|[^\wáéíóúñ])" + re.escape(v_norm) + r"(?:$|\s|[^\wáéíóúñ])"
        if re.search(patron, texto_norm):
            return serv

    return None

