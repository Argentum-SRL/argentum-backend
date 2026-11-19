# -*- coding: utf-8 -*-
"""Prueba TF16: textos actualizados del catálogo y línea nueva del SYSTEM_PROMPT."""
from __future__ import annotations

from tests.test_fallas_bot_m2d import (
    _crear_usuario_completo,
    _entorno_seguro,
    db_fixture,
)
from app.services import ai_service, clasificador_concepto_service

_ = (_entorno_seguro, db_fixture)


def test_tf16_textos_catalogo_y_system_prompt(db):
    """TF16: descripciones de catálogo y línea nueva del SYSTEM_PROMPT llegan a ambos prompts."""
    esc = _crear_usuario_completo(db)
    assert esc["usuario"] is not None
    ai_service.invalidar_cache_schema()
    clasificador_concepto_service.limpiar_caches_clasificador()

    prompt_bot = ai_service._construir_system_prompt_completo(db)
    prompt_eg = clasificador_concepto_service.construir_system_prompt(db, "egreso")
    prompt_in = clasificador_concepto_service.construir_system_prompt(db, "ingreso")

    frases_egreso = [
        "taxi, remis, Uber, uver, Cabify, DiDi",
        (
            "muebles, colchón, heladera, lavarropas, aire acondicionado, "
            "microondas, cocina, electrodomésticos, decoración, bazar"
        ),
        (
            "celular nuevo, notebook, computadora, tablet, auriculares, "
            "televisor, consola, monitor "
            "(heladera, lavarropas y aire van en Muebles y electro)"
        ),
    ]
    frases_ingreso = [
        "sueldo, quincena (es sueldo, no bono), salario, recibo de sueldo",
        "bono, premio, horas extras, comisión, propina que te dejan",
    ]
    for f in frases_egreso:
        assert f in prompt_bot
        assert f in prompt_eg
    for f in frases_ingreso:
        assert f in prompt_bot
        assert f in prompt_in

    linea_nueva = (
        "- Si el mensaje tiene un monto y algo que no conocés (una marca, "
        "una app, un producto o una palabra mal escrita), es un gasto: "
        'intent "registrar_transaccion" con la categoría más probable; '
        'nunca "desconocido".'
    )
    assert ai_service.SYSTEM_PROMPT.count(linea_nueva) == 1
    assert prompt_bot.count(linea_nueva) == 1
