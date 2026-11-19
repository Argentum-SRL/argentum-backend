# -*- coding: utf-8 -*-
"""
tests/test_evaluacion_m2.py — Pruebas sin IA para evaluación y extracción (TE1 a TE5).
"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import pytest

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from scripts.evaluacion import (
    evaluar_audios,
    evaluar_bot,
    evaluar_clasificador,
    evaluar_documentos,
    extraer_mensajes_reales,
)


@pytest.fixture(autouse=True)
def bloquear_openai(monkeypatch):
    """Falla si algún test intenta instanciar un cliente real de OpenAI."""

    def _prohibido(*args, **kwargs):
        raise AssertionError("Prohibido crear cliente real de OpenAI en tests.")

    monkeypatch.setattr("app.services.openai_client.OpenAI", _prohibido)
    monkeypatch.setattr("app.services.openai_client._openai_client", None)


def test_te1_comparar_resultado():
    """TE1: comparar_resultado cubre acierto, monto, cantidad, dias y texto."""
    esp_igual = {
        "intent": "registrar_transaccion",
        "movimientos": [
            {
                "tipo": "egreso",
                "monto": 2000,
                "moneda": "ARS",
                "categoria": "Alimentación",
                "subcategoria": "Supermercado",
                "dias": 0,
            }
        ],
        "texto_contiene": ["anoté en súper"],
    }
    obt_igual = {
        "intent": "registrar_transaccion",
        "movimientos": [
            {
                "tipo": "egreso",
                "monto": 2000.0,
                "moneda": "ARS",
                "cuenta": "Galicia",
                "categoria": "Alimentación",
                "subcategoria": "Supermercado",
                "dias": 0,
            }
        ],
        "mensajes_bot": ["Listo, ANOTE EN SUPER $2.000."],
    }
    res_ok = evaluar_bot.comparar_resultado(esp_igual, obt_igual)
    assert res_ok["estado"] == "ACIERTO"
    assert res_ok["fallos"] == []

    # Monto distinto -> FALLA con "monto"
    obt_monto = {
        "movimientos": [
            {
                "tipo": "egreso",
                "monto": 3000,
                "moneda": "ARS",
                "categoria": "Alimentación",
                "subcategoria": "Supermercado",
                "dias": 0,
            }
        ]
    }
    res_monto = evaluar_bot.comparar_resultado(
        {"movimientos": esp_igual["movimientos"]}, obt_monto
    )
    assert res_monto["estado"] == "FALLA"
    assert "monto" in res_monto["fallos"]

    # Un movimiento de más -> FALLA con "cantidad"
    obt_mas = {
        "movimientos": obt_igual["movimientos"] + obt_igual["movimientos"],
    }
    res_cant = evaluar_bot.comparar_resultado(
        {"movimientos": esp_igual["movimientos"]}, obt_mas
    )
    assert res_cant["estado"] == "FALLA"
    assert "cantidad" in res_cant["fallos"]

    # dias distinto -> FALLA con "dias" (entero)
    res_dias = evaluar_bot.comparar_resultado(
        {"movimientos": [{"dias": -1}]},
        {"movimientos": [{"dias": 0}]},
    )
    assert res_dias["estado"] == "FALLA"
    assert "dias" in res_dias["fallos"]


def test_te2_validar_frases_bot_json():
    """TE2: frases_bot.json valida formato, ids, funciones y catálogo."""
    ruta = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "evaluacion"
        / "frases_bot.json"
    )
    data = json.loads(ruta.read_text(encoding="utf-8"))
    assert data["version"] == 1
    frases = data["frases"]
    assert len(frases) >= 300

    catalogo_por_tipo: dict[str, dict[str, set[str]]] = {"egreso": {}, "ingreso": {}}
    for cat in ARBOL_CATEGORIAS:
        subs = {s.nombre for s in cat.subcategorias}
        catalogo_por_tipo[cat.tipo][cat.nombre] = subs

    ids_vistos: set[str] = set()
    for item in frases:
        fid = item["id"]
        assert fid not in ids_vistos
        ids_vistos.add(fid)
        assert item["funcion"] in evaluar_bot.FUNCIONES_VALIDAS
        assert isinstance(item["mensajes"], list) and len(item["mensajes"]) >= 1
        assert all(isinstance(m, str) and m.strip() for m in item["mensajes"])
        for mov in item["esperado"].get("movimientos", []):
            tipo = mov.get("tipo", "egreso")
            cat_nom = mov.get("categoria")
            sub_nom = mov.get("subcategoria")
            if cat_nom:
                assert cat_nom in catalogo_por_tipo[tipo]
                if sub_nom:
                    assert sub_nom in catalogo_por_tipo[tipo][cat_nom]


def test_te3_tope_dos_dolares():
    """TE3: con 1.99 sigue y al pasar 2.00 corta con el mensaje fijo."""
    esperado_msg = "Se cortó: pasó el tope de USD 2 de esta corrida."
    for mod in (
        evaluar_bot,
        evaluar_documentos,
        evaluar_audios,
        evaluar_clasificador,
    ):
        assert mod.supera_tope_corrida(Decimal("1.99")) is False
        assert mod.supera_tope_corrida(Decimal("2.00")) is False
        assert mod.supera_tope_corrida(Decimal("2.01")) is True
        assert mod.MENSAJE_TOPE_CORRIDA == esperado_msg


def test_te4_wer_evaluar_audios():
    """TE4: wer iguales -> 0, 1 cambiada en 10 -> 0.1, normaliza tildes/puntuación."""
    assert evaluar_audios.wer("Gasté dos mil pesos", "¡gaste dos mil pesos!") == 0.0
    ref = "uno dos tres cuatro cinco seis siete ocho nueve diez"
    hyp = "Uno, dos, tres, cuatro, CINCO, seis, siete, ocho, nueve, once."
    assert evaluar_audios.wer(ref, hyp) == pytest.approx(0.1)


def test_te5_anonimizar():
    """TE5: reemplaza datos sensibles y deja intactos montos normales."""
    assert "@" not in extraer_mensajes_reales.anonimizar("mi mail es juan@gmail.com")
    assert "1234" not in extraer_mensajes_reales.anonimizar("llamame al +54 9 11 4567-1234")
    assert "4567" not in extraer_mensajes_reales.anonimizar("tel 1145678901")
    assert "1234567" not in extraer_mensajes_reales.anonimizar("dni 1234567")
    assert "0000003100012345678901" not in extraer_mensajes_reales.anonimizar(
        "cbu 0000003100012345678901"
    )
    assert "juan.perez.mp" not in extraer_mensajes_reales.anonimizar(
        "transferi a juan.perez.mp"
    )
    assert "https://" not in extraer_mensajes_reales.anonimizar(
        "mira https://ejemplo.com/ticket?id=123"
    )
    assert (
        extraer_mensajes_reales.anonimizar("gasté 2500 en el súper")
        == "gasté 2500 en el súper"
    )
    assert (
        extraer_mensajes_reales.anonimizar("pagué 20.000 con la visa")
        == "pagué 20.000 con la visa"
    )


def test_te6_clasificar_primera_respuesta():
    """TE6: clasifica anoto_directo, pide_confirmacion u otra sobre primer mensaje."""
    assert (
        evaluar_bot.clasificar_primera_respuesta(["Listo, anoté $9.000 ..."])
        == "anoto_directo"
    )
    assert (
        evaluar_bot.clasificar_primera_respuesta(["¿Anoto $9.000 ...?"])
        == "pide_confirmacion"
    )
    assert (
        evaluar_bot.clasificar_primera_respuesta(["¿Lo anoto ...?"])
        == "pide_confirmacion"
    )
    assert (
        evaluar_bot.clasificar_primera_respuesta(["¿Las anoto ...?"])
        == "pide_confirmacion"
    )
    assert (
        evaluar_bot.clasificar_primera_respuesta(["¿Aporto $20.000 ...?"])
        == "pide_confirmacion"
    )
    assert (
        evaluar_bot.clasificar_primera_respuesta(["¿Lo dejo en ...?"])
        == "pide_confirmacion"
    )
    assert (
        evaluar_bot.clasificar_primera_respuesta(["No te entendí..."])
        == "otra"
    )
    assert evaluar_bot.clasificar_primera_respuesta([]) == "otra"


def test_te7_tabla_calibracion():
    """TE7: tabla_calibracion sobre simples ARS sin cuotas."""
    resultados = [
        {
            "funcion": "gasto",
            "esperado": {"movimientos": [{"tipo": "egreso", "moneda": "ARS"}]},
            "confianzas_ia": [0.6],
            "comparacion": {"fallos": [], "campos_ok": {"monto": True}},
        },
        {
            "funcion": "ingreso",
            "esperado": {"movimientos": [{"tipo": "ingreso"}]},
            "confianzas_ia": [0.9],
            "comparacion": {"fallos": [], "campos_ok": {"monto": True}},
        },
        {
            "funcion": "gasto",
            "esperado": {"movimientos": [{"tipo": "egreso"}]},
            "confianzas_ia": [0.6],
            "comparacion": {"fallos": ["monto"], "campos_ok": {"monto": False}},
        },
        {
            "funcion": "cuotas",
            "esperado": {"movimientos": [{"tipo": "egreso", "cuotas": 6}]},
            "confianzas_ia": [0.6],
            "comparacion": {"fallos": ["monto"], "campos_ok": {"monto": False}},
        },
    ]
    tabla = evaluar_bot.tabla_calibracion(resultados)
    por_u = {round(f["umbral"], 2): f for f in tabla}
    assert por_u[0.70]["preguntaria"] == 2
    assert por_u[0.70]["errores_plata_atrapados"] == 1
    assert por_u[0.70]["errores_plata_sin_atrapar"] == 0
    assert por_u[0.50]["preguntaria"] == 0
    assert por_u[0.50]["errores_plata_atrapados"] == 0
    assert por_u[0.50]["errores_plata_sin_atrapar"] == 1


def test_te8_proc_medido_guarda_confianza_e_intent(monkeypatch, tmp_path):
    """TE8: _proc_medido guarda confianza e intent falsos en confianzas_ia e intents_ia."""
    from scripts.regresion.suite import comun

    tiempos: list[float] = []
    estado = {
        "costo": Decimal("0"),
        "llamadas": 0,
        "tiempos": [],
        "errores": [],
        "confianzas": [],
        "intents_ia": [],
    }

    def _ia_falsa(mensaje, usuario, db, historial=None, estado_previo=None):
        return {"intent": "registrar_transaccion", "confianza": Decimal("0.72")}

    proc = evaluar_bot._crear_proc_medido(_ia_falsa, estado, tiempos)
    res = proc("almuerzo 9 lucas", object(), object())
    assert res["intent"] == "registrar_transaccion"
    assert estado["confianzas"] == [0.72]
    assert estado["intents_ia"] == ["registrar_transaccion"]
    assert estado["llamadas"] == 1

    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://postgres:x@localhost:5433/argentum_local"
    )
    monkeypatch.setattr(evaluar_bot, "OUT_DIR", tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt: "SI")
    monkeypatch.setattr(comun, "_original_procesar_mensaje", _ia_falsa)

    def _exec_fake(frase_cfg):
        comun._original_procesar_mensaje("m", object(), object())
        return {
            "intent": "registrar_transaccion",
            "movimientos": [],
            "transferencias": [],
            "mensajes_bot": ["Listo, anoté $9.000"],
            "problemas_texto_ia": [],
        }

    monkeypatch.setattr(evaluar_bot, "_ejecutar_frase", _exec_fake)
    rc = evaluar_bot.main(["--solo", "B001"])
    assert rc == 0
    json_files = list(tmp_path.glob("evaluar_bot_*.json"))
    assert len(json_files) == 1
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert len(data["resultados"]) == 1
    r0 = data["resultados"][0]
    assert r0["confianzas_ia"] == [0.72]
    assert r0["intents_ia"] == ["registrar_transaccion"]
    assert r0["primera_respuesta"] == "anoto_directo"
    assert "calibracion" in data
