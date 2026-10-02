"""
Tests unitarios para las funciones puras de scripts/local/paso.py.
Se ejecutan con tmp_path y sin acceso a base de datos.
"""
from pathlib import Path
import pytest

from scripts.local.paso import (
    comparar_ast_archivo,
    extraer_simbolos_ast,
    verificar_permitidas,
    comparar_mensajes_pyflakes,
    comparar_lineas_alerta,
    verificar_tamanio_lineas,
    verificar_misma_fecha,
    clasificar_estado_git,
)


def test_detectar_funciones_cambiada_nueva_borrada():
    code_antes = """
def func_a():
    return 1

def func_b():
    return 2
"""
    code_despues = """
def func_a():
    return 999  # CAMBIADA

def func_c():   # NUEVA
    return 3
# func_b fue BORRADA
"""
    cambios = comparar_ast_archivo(code_antes, code_despues)
    assert cambios["func_a"] == "CAMBIADA"
    assert cambios["func_c"] == "NUEVA"
    assert cambios["func_b"] == "BORRADA"


def test_detectar_metodo_clase_y_funcion_interna():
    code_antes = """
class Calculadora:
    def sumar(self, a, b):
        def helper():
            return a + b
        return helper()
"""
    code_despues = """
class Calculadora:
    def sumar(self, a, b):
        def helper():
            return (a + b) * 2  # helper CAMBIADA
        return helper()
"""
    cambios = comparar_ast_archivo(code_antes, code_despues)
    assert "Calculadora.sumar" in cambios
    assert "Calculadora.sumar.helper" in cambios
    assert cambios["Calculadora.sumar.helper"] == "CAMBIADA"
    assert cambios["Calculadora.sumar"] == "CAMBIADA"


def test_detectar_import_nuevo_nivel_modulo():
    code_antes = """
import math

X = 10
"""
    code_despues = """
import math
import os  # nuevo import

X = 10
"""
    cambios = comparar_ast_archivo(code_antes, code_despues)
    assert "<modulo>" in cambios
    assert cambios["<modulo>"] == "CAMBIADA"


def test_control_permitidas_fuera_de_lista_y_wildcard(tmp_path):
    permitidas_path = tmp_path / "permitidas.txt"
    permitidas_path.write_text(
        "app/servicio.py::*\n"
        "app/router.py::funcion_permitida\n",
        encoding="utf-8",
    )
    permitidas_text = permitidas_path.read_text(encoding="utf-8")

    cambios = {
        "app/servicio.py": [
            ("funcion_cualquiera", "CAMBIADA"),
            ("<modulo>", "CAMBIADA"),
        ],
        "app/router.py": [
            ("funcion_permitida", "CAMBIADA"),
            ("funcion_no_permitida", "NUEVA"),
        ],
    }

    ok, fuera = verificar_permitidas(cambios, permitidas_text)
    assert ok is False
    assert any("app/router.py::funcion_no_permitida (NUEVA)" in f for f in fuera)
    assert not any("app/servicio.py" in f for f in fuera)  # cubierto por app/servicio.py::*
    assert not any("funcion_permitida" in f for f in fuera)


def test_pyflakes_mensaje_nuevo_vs_existente():
    code_antes = """
import sys  # unused import previo
"""
    code_despues = """
import sys  # unused import previo
def foo():
    return variable_no_definida_xyz  # nuevo UndefinedName
"""
    nuevos, undef = comparar_mensajes_pyflakes(code_antes, code_despues)
    # El unused import sys ya existía, no debe figurar como nuevo
    assert not any("'sys' imported but unused" in m for m in nuevos)
    # La variable no definida es nueva y debe marcarse en undefined
    assert len(undef) > 0
    assert any("variable_no_definida_xyz" in m for m in undef)


def test_linea_fallo_nueva_vs_repetida():
    salidas_inicio = """
=== INICIO HERRAMIENTAS ===
[FALLO] Error previo conocido en modulo X
Fin
"""
    salidas_cierre = """
=== CIERRE HERRAMIENTAS ===
[FALLO] Error previo conocido en modulo X
[FALLO] Error nuevo inesperado en modulo Y
Fin
"""
    nuevas = comparar_lineas_alerta(salidas_inicio, salidas_cierre)
    assert len(nuevas) == 1
    assert "Error nuevo inesperado en modulo Y" in nuevas[0]
    assert not any("Error previo conocido" in n for n in nuevas)


def test_archivo_mas_de_1300_lineas():
    archivos = {
        "app/routers/valido.py": 1200,
        "app/services/muy_largo.py": 1305,
    }
    ok, excedidos = verificar_tamanio_lineas(archivos, max_lineas=1300)
    assert ok is False
    assert excedidos == [("app/services/muy_largo.py", 1305)]


def test_fecha_distinta():
    assert verificar_misma_fecha("2026-10-02", "2026-10-02") is True
    assert verificar_misma_fecha("2026-10-02", "2026-10-03") is False


def test_clasificar_estado_git():
    assert clasificar_estado_git(" M app/test.py", 0) == "CAMBIOS SIN COMITEAR"
    assert clasificar_estado_git("", 2) == "COMMITS SIN PUSH"
    assert clasificar_estado_git("", 0) == "SINCRONIZADO"
