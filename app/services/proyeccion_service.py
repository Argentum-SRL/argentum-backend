"""
Servicio de proyecciones financieras de Argentum.

Módulo de delegación hacia la proyección probabilística calibrada
implementada en app.services.analisis_financiero_service.
"""
from __future__ import annotations

from typing import Any, Dict
from sqlalchemy.orm import Session

from app.models.usuario import Usuario
from app.services.analisis_financiero_service import calcular_proyeccion_nueva


def calcular_proyeccion(db: Session, usuario: Usuario) -> Dict[str, Any]:
    """
    Calcula la proyección financiera del usuario para el ciclo actual.
    Delega en el motor probabilístico calibrado de analisis_financiero_service.
    """
    return calcular_proyeccion_nueva(db, usuario)
