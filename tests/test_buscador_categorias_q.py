# -*- coding: utf-8 -*-
"""
tests/test_buscador_categorias_q.py — Pruebas puras del buscador (TQ1 a TQ4 y TQ13).
"""
from __future__ import annotations

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.core.reglas_texto_web import problemas_texto_web
from app.services.categoria_busqueda_service import (
    buscar_en_catalogo,
    distancia_tipeo,
)


def _subcategorias_sistema() -> set[tuple[str, str]]:
    """Devuelve pares (categoria, subcategoria) marcados como es_sistema."""
    sistema: set[tuple[str, str]] = set()
    for cat in ARBOL_CATEGORIAS:
        for sub in cat.subcategorias:
            if sub.es_sistema:
                sistema.add((cat.nombre, sub.nombre))
    return sistema


def _categorias_por_tipo(tipo: str) -> set[str]:
    """Devuelve nombres de categorías del árbol para un tipo dado."""
    return {cat.nombre for cat in ARBOL_CATEGORIAS if cat.tipo == tipo}


def _verificar_invariantes(resultados: list[tuple[str, str | None]], tipo: str) -> None:
    """Comprueba límite de 8, sin repetidos, sin Ahorro, sin es_sistema y tipo válido."""
    assert len(resultados) <= 8
    assert len(resultados) == len(set(resultados))
    sistema = _subcategorias_sistema()
    validas = _categorias_por_tipo(tipo)
    for cat, sub in resultados:
        assert cat != "Ahorro"
        assert cat in validas
        if sub is not None:
            assert (cat, sub) not in sistema


def test_tq1_egreso_primer_resultado_y_vacios():
    """TQ1: búsquedas en egreso y casos que devuelven lista vacía."""
    casos = [
        ("pescadería", ("Alimentación", "Carnicería")),
        ("pescaderia", ("Alimentación", "Carnicería")),
        ("pescadreia", ("Alimentación", "Carnicería")),
        ("nafta", ("Transporte", "Combustible")),
        ("heladera", ("Hogar", "Muebles y electro")),
        ("heladeria", ("Gastronomía", "Cafetería")),
        ("chino", ("Alimentación", "Supermercado")),
        ("cerveza", ("Ocio", "Salidas")),
        ("super", ("Alimentación", "Supermercado")),
        ("Súper", ("Alimentación", "Supermercado")),
        ("vinoteca", ("Alimentación", None)),
    ]
    for texto, esperado in casos:
        res = buscar_en_catalogo(texto, "egreso")
        _verificar_invariantes(res, "egreso")
        assert res, f"Sin resultados para '{texto}'"
        assert res[0] == esperado, f"Para '{texto}' salió {res[0]} en lugar de {esperado}"

    for texto_vacio in ("xyzzy", "nafa", "a", "  "):
        res_vacio = buscar_en_catalogo(texto_vacio, "egreso")
        assert res_vacio == [], f"Para '{texto_vacio}' debía dar [] y dio {res_vacio}"


def test_tq2_filtro_por_tipo():
    """TQ2: las búsquedas filtran estrictamente por tipo egreso/ingreso."""
    res_com_eg = buscar_en_catalogo("comision", "egreso")
    _verificar_invariantes(res_com_eg, "egreso")
    assert res_com_eg[0] == ("Banco", "Comisiones")
    cats_ingreso_solo = _categorias_por_tipo("ingreso") - _categorias_por_tipo("egreso")
    assert all(cat not in cats_ingreso_solo for cat, _ in res_com_eg)

    res_com_in = buscar_en_catalogo("comision", "ingreso")
    _verificar_invariantes(res_com_in, "ingreso")
    assert res_com_in[0] == ("Trabajo", "Bonos y extras")

    res_int_eg = buscar_en_catalogo("intereses", "egreso")
    _verificar_invariantes(res_int_eg, "egreso")
    assert res_int_eg[0] == ("Banco", "Intereses")

    res_int_in = buscar_en_catalogo("intereses", "ingreso")
    _verificar_invariantes(res_int_in, "ingreso")
    assert res_int_in[0] == ("Inversiones", "Intereses")

    res_sueldo_in = buscar_en_catalogo("sueldo", "ingreso")
    _verificar_invariantes(res_sueldo_in, "ingreso")
    assert res_sueldo_in[0] == ("Trabajo", "Sueldo")

    assert buscar_en_catalogo("sueldo", "egreso") == []


def test_tq3_categoria_en_orden_e_invariantes():
    """TQ3: buscar por nombre de categoría devuelve todas sus subcategorías en orden."""
    res = buscar_en_catalogo("transporte", "egreso")
    _verificar_invariantes(res, "egreso")
    esperado = [
        ("Transporte", "Transporte público"),
        ("Transporte", "Taxi y apps"),
        ("Transporte", "Combustible"),
        ("Transporte", "Estacionamiento"),
        ("Transporte", "Peajes"),
        ("Transporte", "Auto"),
    ]
    assert res == esperado


def test_tq4_distancia_tipeo():
    """TQ4: Damerau-Levenshtein con transposición de letras vecinas."""
    assert distancia_tipeo("pescaderia", "pescadreia") == 1
    assert distancia_tipeo("nafta", "nafa") == 1
    assert distancia_tipeo("casa", "casa") == 0
    assert distancia_tipeo("abc", "xyz") == 3


def test_tq13_textos_front_cumplen_reglas_texto_web():
    """TQ13: los textos del front pasan problemas_texto_web sin errores."""
    frases = [
        "Elegida según la descripción. Podés cambiarla.",
        "No hay categorías con ese nombre.",
        "No se pudo buscar. Probá de nuevo.",
    ]
    for frase in frases:
        assert problemas_texto_web(frase) == []
