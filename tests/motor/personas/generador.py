"""Generador principal de personas sintéticas para pruebas del motor de finanzas.

Reúne los modelos, catálogo, base temporal y los generadores de perfiles
base (P01-P05) y avanzados (P06-P10).
"""

from typing import Callable, Dict, List

from tests.motor.personas.base_tiempo import (
    FECHA_FIN_HISTORIA,
    ContextoGeneracionPersona,
    obtener_ciclos_12_meses,
)
from tests.motor.personas.modelos import Persona
from tests.motor.personas.perfiles_avanzados import (
    generar_p06,
    generar_p07,
    generar_p08,
    generar_p09,
    generar_p10,
)
from tests.motor.personas.perfiles_base import (
    generar_p01,
    generar_p02,
    generar_p03,
    generar_p04,
    generar_p05,
)

# Alias para compatibilidad
generar_12_ciclos = obtener_ciclos_12_meses

# Diccionario de despachadores por código de persona
GENERADORES_POR_PERSONA: Dict[str, Callable[[ContextoGeneracionPersona], None]] = {
    "P01": generar_p01,
    "P02": generar_p02,
    "P03": generar_p03,
    "P04": generar_p04,
    "P05": generar_p05,
    "P06": generar_p06,
    "P07": generar_p07,
    "P08": generar_p08,
    "P09": generar_p09,
    "P10": generar_p10,
}

# Metadatos base y semillas fijas para reproducibilidad
DEFINICION_PERSONAS: List[dict] = [
    {
        "id": "P01",
        "nombre": "Empleado en blanco, sueldo fijo",
        "descripcion": "Empleado en blanco, sueldo fijo sin aumentos, alquila, ingreso de 1.200.000",
        "tipo_ingreso": "regular",
        "cobertura_real": 1.0,
        "semilla": 1001,
    },
    {
        "id": "P02",
        "nombre": "Empleado con paritarias y aguinaldo",
        "descripcion": "Empleado con paritarias cada 2 a 4 meses y aguinaldo, propietario, 2.800.000",
        "tipo_ingreso": "regular",
        "cobertura_real": 1.0,
        "semilla": 1002,
    },
    {
        "id": "P03",
        "nombre": "Freelance en pesos con cobros irregulares",
        "descripcion": "Freelance en pesos, cobros irregulares en monto y en fecha, promedio de 1.500.000",
        "tipo_ingreso": "variable",
        "cobertura_real": 1.0,
        "semilla": 1003,
    },
    {
        "id": "P04",
        "nombre": "Freelance en dólares",
        "descripcion": "Freelance que cobra en dólares (unos USD 1.500 por mes) y gasta en pesos",
        "tipo_ingreso": "variable",
        "cobertura_real": 1.0,
        "semilla": 1004,
    },
    {
        "id": "P05",
        "nombre": "Estudiante mantenido",
        "descripcion": "Estudiante mantenido: padres transfieren montos parecidos sin día fijo, unos 600.000 por mes",
        "tipo_ingreso": "intermitente",
        "cobertura_real": 1.0,
        "semilla": 1005,
    },
    {
        "id": "P06",
        "nombre": "Pasante con gustos",
        "descripcion": "Pasante con ingreso fijo chico (650.000) y gastos de gustos",
        "tipo_ingreso": "regular",
        "cobertura_real": 1.0,
        "semilla": 1006,
    },
    {
        "id": "P07",
        "nombre": "Jefa de hogar numeroso",
        "descripcion": "Jefa de un hogar de 7 personas: súper muy alto, colegio, prepaga familiar, 2.500.000",
        "tipo_ingreso": "regular",
        "cobertura_real": 1.0,
        "semilla": 1007,
    },
    {
        "id": "P08",
        "nombre": "Jubilado con movilidad previsional",
        "descripcion": "Jubilado: haber sube cada mes según inflación de 2 meses antes, más bono fijo de 70.000",
        "tipo_ingreso": "indexado",
        "cobertura_real": 1.0,
        "semilla": 1008,
    },
    {
        "id": "P09",
        "nombre": "Empleado subregistrador (cobertura 50%)",
        "descripcion": "Igual que P01, pero registra solo alrededor del 50% de gastos del día a día y costumbres",
        "tipo_ingreso": "regular",
        "cobertura_real": 0.5,
        "semilla": 1009,
    },
    {
        "id": "P10",
        "nombre": "Monotributista con ingresos variables",
        "descripcion": "Monotributista con cuota mensual fija de monotributo e ingresos variables",
        "tipo_ingreso": "variable",
        "cobertura_real": 1.0,
        "semilla": 1010,
    },
]


def generar_persona(codigo_o_definicion, semilla: int = None) -> Persona:
    """Genera una persona sintética por su código (ej: 'P01') o diccionario de definición.

    Permite sobrescribir la semilla para pruebas de reproducibilidad o variación.
    """
    if isinstance(codigo_o_definicion, str):
        definicion = next(
            (p for p in DEFINICION_PERSONAS if p["id"] == codigo_o_definicion), None
        )
        if definicion is None:
            raise ValueError(f"Persona no encontrada: {codigo_o_definicion}")
    else:
        definicion = codigo_o_definicion

    semilla_usada = semilla if semilla is not None else definicion["semilla"]
    ciclos = obtener_ciclos_12_meses()
    contexto = ContextoGeneracionPersona(
        persona_id=definicion["id"],
        semilla=semilla_usada,
    )

    generador_fn = GENERADORES_POR_PERSONA.get(definicion["id"])
    if not generador_fn:
        raise NotImplementedError(
            f"No se implementó el generador para {definicion['id']}"
        )

    generador_fn(contexto)
    contexto.movimientos.sort(key=lambda m: (m.fecha, m.id))

    return Persona(
        id=definicion["id"],
        nombre=definicion["nombre"],
        descripcion_perfil=definicion.get("descripcion", definicion["nombre"]),
        semilla=semilla_usada,
        tipo_ingreso=definicion["tipo_ingreso"],
        cobertura_real=definicion["cobertura_real"],
        ciclos=ciclos,
        movimientos=contexto.movimientos,
        grupos_verdad=contexto.grupos,
    )


def generar_todas_las_personas() -> List[Persona]:
    """Genera la lista completa de las 10 personas sintéticas con sus semillas fijas."""
    return [generar_persona(definicion) for definicion in DEFINICION_PERSONAS]
