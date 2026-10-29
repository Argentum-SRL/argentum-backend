# -*- coding: utf-8 -*-
"""
aviso_frescura_wpp.py - Generación del aviso de frescura para respuestas de WhatsApp.

Produce una línea breve y no invasiva al pie de la respuesta a 'consultar_saldo'
cuando los datos del usuario superan su umbral adaptativo de inactividad.
"""
from __future__ import annotations

from app.services.frescura_service import Frescura


def linea_frescura_wpp(frescura: Frescura) -> str | None:
    """
    Retorna la línea de aviso de frescura para WhatsApp si los datos están viejos;
    None si los datos están al día o no hay suficiente evidencia de antigüedad.
    Exactamente 90 caracteres o menos y sin signos de interrogación.
    """
    if not frescura.datos_viejos:
        return None

    return f"Ojo: hace {frescura.dias_sin_cargar} días que no cargás nada, puede no estar al día."

