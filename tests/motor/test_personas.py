"""Pruebas unitarias de integridad de las personas sintéticas y su verdad conocida.

Verifica:
1. Misma semilla produce el mismo hash de contenido (reproducibilidad).
2. 12 meses de historia por persona.
3. Ninguna fecha posterior al 31/08/2026.
4. Todos los movimientos tienen etiqueta de verdad y grupo asignado.
5. Todos los montos son instancias de Decimal válidas.

Nota: No evalúa el acierto o lógica del clasificador del motor.
"""

from datetime import date
from decimal import Decimal
import pytest

from tests.motor.personas import DEFINICION_PERSONAS, generar_persona, generar_todas_las_personas
from tests.motor.personas.base_tiempo import FECHA_FIN_HISTORIA, MESES_HISTORIA


@pytest.fixture(scope="module")
def personas_todas():
    """Genera las 10 personas sintéticas una vez para la suite del módulo."""
    return generar_todas_las_personas()


@pytest.mark.parametrize("definicion", DEFINICION_PERSONAS, ids=[d["id"] for d in DEFINICION_PERSONAS])
def test_misma_semilla_da_mismo_hash_contenido(definicion):
    """Chequeo 1: Misma semilla debe generar exactamente el mismo hash de contenido."""
    p1 = generar_persona(definicion["id"], semilla=definicion["semilla"])
    p2 = generar_persona(definicion["id"], semilla=definicion["semilla"])

    hash1 = p1.calcular_hash_contenido()
    hash2 = p2.calcular_hash_contenido()

    assert hash1 == hash2, f"Discrepancia de hash para {definicion['id']}: {hash1} != {hash2}"
    assert len(p1.movimientos) == len(p2.movimientos), "Discrepancia en cantidad de movimientos generados"


def test_12_meses_por_persona(personas_todas):
    """Chequeo 2: Cada persona debe tener 12 ciclos y cubrir los 12 meses de historia."""
    meses_esperados = set(MESES_HISTORIA)

    for p in personas_todas:
        # Verificar cantidad de ciclos
        assert len(p.ciclos) == 12, f"{p.id} tiene {len(p.ciclos)} ciclos en lugar de 12"

        # Verificar que las transacciones cubren los 12 meses
        meses_presentes = {(m.fecha.year, m.fecha.month) for m in p.movimientos}
        assert meses_presentes == meses_esperados, (
            f"{p.id} no cubre los 12 meses esperados. Faltan: {meses_esperados - meses_presentes}"
        )


def test_ninguna_fecha_posterior_al_31_agosto_2026(personas_todas):
    """Chequeo 3: Ninguna fecha de movimiento puede ser posterior al 31/08/2026."""
    limite = FECHA_FIN_HISTORIA
    assert limite == date(2026, 8, 31)

    for p in personas_todas:
        for m in p.movimientos:
            assert m.fecha <= limite, (
                f"Movimiento {m.id} de {p.id} tiene fecha futura inválida: {m.fecha} > {limite}"
            )


def test_todos_los_movimientos_tienen_etiqueta_de_verdad(personas_todas):
    """Chequeo 4: Todos los movimientos tienen etiqueta de verdad y grupo válido."""
    tipos_verdad_validos = {
        "ingreso_habitual",
        "ingreso_extra",
        "gasto_fijo",
        "gasto_diario",
        "costumbre",
        "eventual",
        "obligacion_declarada",
    }

    for p in personas_todas:
        # Verificar que hay grupos de verdad registrados
        assert len(p.grupos_verdad) > 0, f"{p.id} no tiene grupos de verdad definidos"

        for m in p.movimientos:
            assert m.tipo_verdadero in tipos_verdad_validos, (
                f"Movimiento {m.id} de {p.id} tiene tipo de verdad inválido: {m.tipo_verdadero}"
            )
            assert bool(m.grupo_verdadero), f"Movimiento {m.id} de {p.id} tiene grupo_verdadero vacío"
            assert m.grupo_verdadero in p.grupos_verdad, (
                f"Movimiento {m.id} de {p.id} referencia grupo '{m.grupo_verdadero}' no registrado en grupos_verdad"
            )


def test_montos_en_decimal(personas_todas):
    """Chequeo 5: Todos los montos deben ser instancias de Decimal y estrictamente positivos."""
    for p in personas_todas:
        for m in p.movimientos:
            assert isinstance(m.monto, Decimal), (
                f"Movimiento {m.id} de {p.id} tiene monto de tipo {type(m.monto)} en lugar de Decimal"
            )
            assert m.monto > Decimal("0"), (
                f"Movimiento {m.id} de {p.id} tiene monto no positivo: {m.monto}"
            )
