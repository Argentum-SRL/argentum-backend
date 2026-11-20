# -*- coding: utf-8 -*-
"""
Tests unitarios puros para clasificar_tabla y TABLAS_DE_USO de scripts/local/probar_restauracion.py.
Sin base de datos, sin red y sin subprocesos.
"""
from __future__ import annotations

from scripts.local.probar_restauracion import TABLAS_DE_USO, clasificar_tabla


def test_r1_clasificar_tabla_igual():
    """R1: cantidades iguales devuelven IGUAL."""
    assert clasificar_tabla("transacciones", 2864, 2864) == "IGUAL"


def test_r2_clasificar_tabla_distinta_en_tabla_de_datos():
    """R2: tabla de datos con distinta cantidad devuelve DISTINTA."""
    assert clasificar_tabla("transacciones", 2864, 2865) == "DISTINTA"


def test_r3_clasificar_tabla_distinta_por_uso():
    """R3: tablas de uso con distinta cantidad devuelven DISTINTA POR USO."""
    assert clasificar_tabla("rate_limits", 1, 12) == "DISTINTA POR USO"
    assert clasificar_tabla("eventos_actualizacion", 913, 915) == "DISTINTA POR USO"


def test_r4_clasificar_tabla_de_uso_igual():
    """R4: tabla de uso con misma cantidad devuelve IGUAL."""
    assert clasificar_tabla("rate_limits", 5, 5) == "IGUAL"


def test_r5_tablas_de_uso_exacto_y_sin_tablas_de_personas():
    """R5: TABLAS_DE_USO tiene los 13 nombres exactos y ninguna tabla de datos de personas."""
    esperadas = frozenset(
        {
            "rate_limits",
            "eventos_actualizacion",
            "mensajes_salientes_wpp",
            "mensajes_whatsapp_procesados",
            "conversaciones_wpp",
            "uso_ia",
            "ejecuciones_jobs",
            "user_refresh_tokens",
            "codigos_verificacion",
            "claves_idempotencia",
            "eventos_uso",
            "notificaciones",
            "cotizaciones_dolar",
        }
    )
    assert TABLAS_DE_USO == esperadas
    assert len(TABLAS_DE_USO) == 13

    tablas_personas = (
        "usuarios",
        "transacciones",
        "billeteras",
        "cuotas",
        "metas",
        "presupuestos",
        "tarjetas_credito",
        "transferencias_internas",
        "suscripciones",
        "movimientos_meta",
    )
    for tabla in tablas_personas:
        assert tabla not in TABLAS_DE_USO
