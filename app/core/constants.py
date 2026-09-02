"""
app/core/constants.py — Constantes globales del sistema Argentum.
"""
from typing import FrozenSet

# Categorías de sistema reservadas que no deben ser ofrecidas en flujos de IA
# ni en la creación/edición manual de transacciones.
CATEGORIAS_SISTEMA: FrozenSet[str] = frozenset({"Ahorro"})
