# -*- coding: utf-8 -*-
"""
scripts/regresion/suite/escenarios_p18b.py

Escenarios P18B (P18B.1 a P18B.4): Bloque T1 ¿Me lo puedo permitir? por WhatsApp.
"""
from __future__ import annotations

import time
from unittest.mock import patch

from sqlalchemy import select, text

from app.models.conversacion_wpp import ConversacionWpp
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.services import ai_service
from scripts.regresion.suite.comun import (
    TELEFONO_TEST,
    USUARIO_PRUEBAS_EMAIL,
    make_payload,
    run_isolated,
)


def _crear_mock_ia():
    ia_llamada = [False]

    def _mock_procesar(*args, **kwargs):
        ia_llamada[0] = True
        return {
            "intent": "desconocido",
            "entidades": {},
            "confianza": 0.5,
            "slot_filling": False,
            "datos_faltantes": [],
            "respuesta_usuario": "IA",
        }

    return ia_llamada, _mock_procesar


def _preparar_wpp(conn, uid) -> None:
    conn.execute(
        text(
            "UPDATE conversaciones_wpp SET slot_filling_activo = false, "
            "accion_ejecutada = 'test' WHERE usuario_id = :uid"
        ),
        {"uid": uid},
    )


def _ultima_intent(session, uid) -> str:
    conv = session.execute(
        select(ConversacionWpp)
        .where(ConversacionWpp.usuario_id == uid)
        .order_by(ConversacionWpp.fecha.desc())
        .limit(1)
    ).scalar_one_or_none()
    return conv.intent_detectado if conv and conv.intent_detectado else ""


def p18b_caso_1(datos):
    """P18B.1: me da para una tele de 300 mil?"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_wpp(conn, u.id)
        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "me da para una tele de 300 mil?"),
                time.perf_counter(),
            )
        r1 = respuestas[-1][1] if respuestas else ""
        primera = r1.split("\n")[0] if r1 else ""
        s = Session()
        intent = _ultima_intent(s, u.id)
        return f"Intent: {intent} | Primera línea: {primera} | Llegó a la IA: {ia_flag[0]}"

    return run_isolated(test)


def p18b_caso_2(datos):
    """P18B.2: ¿me lo puedo permitir? seguido de 300 mil."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_wpp(conn, u.id)
        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "¿me lo puedo permitir?"),
                time.perf_counter(),
            )
            r1 = respuestas[-1][1] if respuestas else ""
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "300 mil"),
                time.perf_counter(),
            )
        s = Session()
        intent2 = _ultima_intent(s, u.id)
        if "Decime cuánto sale." not in r1:
            raise AssertionError(f"Faltó 'Decime cuánto sale.' en r1: {r1!r}")
        return f"{r1} | Intent2: {intent2} | Llegó a la IA: {ia_flag[0]}"

    return run_isolated(test)


def p18b_caso_3(datos):
    """P18B.3: me puedo permitir un viaje de 1000 dólares?"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_wpp(conn, u.id)
        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "me puedo permitir un viaje de 1000 dólares?"),
                time.perf_counter(),
            )
        r1 = respuestas[-1][1] if respuestas else ""
        s = Session()
        intent = _ultima_intent(s, u.id)
        linea_cot = next(
            (ln for ln in r1.split("\n") if "Lo calculé con el dólar oficial de hoy" in ln),
            "",
        )
        primera = r1.split("\n")[0] if r1 else ""
        return (
            f"Intent: {intent} | Primera línea: {primera} | "
            f"Cotización: {linea_cot} | Llegó a la IA: {ia_flag[0]}"
        )

    return run_isolated(test)


def p18b_caso_4(datos):
    """P18B.4: me puedo permitir pagar el gimnasio de 40 mil por mes?"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_wpp(conn, u.id)
        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(
                    TELEFONO_TEST,
                    "me puedo permitir pagar el gimnasio de 40 mil por mes?",
                ),
                time.perf_counter(),
            )
        r1 = respuestas[-1][1] if respuestas else ""
        primera = r1.split("\n")[0] if r1 else ""
        s = Session()
        intent = _ultima_intent(s, u.id)
        return f"Intent: {intent} | Primera línea: {primera} | Llegó a la IA: {ia_flag[0]}"

    return run_isolated(test)
