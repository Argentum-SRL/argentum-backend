# Paquete de personas sintéticas con verdad conocida para testing del motor
from tests.motor.personas.generador import (
    DEFINICION_PERSONAS,
    generar_persona,
    generar_todas_las_personas,
    obtener_ciclos_12_meses,
)
from tests.motor.personas.modelos import MovimientoSintetico, Persona

__all__ = [
    "DEFINICION_PERSONAS",
    "generar_persona",
    "generar_todas_las_personas",
    "obtener_ciclos_12_meses",
    "MovimientoSintetico",
    "Persona",
]
