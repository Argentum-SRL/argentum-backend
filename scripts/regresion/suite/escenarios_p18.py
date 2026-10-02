"""
scripts/regresion/suite/escenarios_p18.py

Escenarios del Punto 18: Consulta determinística '¿Me lo puedo permitir?' por WhatsApp.
Verifica la paridad de números con la web (tools_service.calcular_puede_permitirse)
para testingadmin.
"""
from __future__ import annotations

import time
from sqlalchemy import text

from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.routers.whatsapp.handlers_permitirse import (
    armar_respuesta_permitirse,
    detectar_consulta_permitirse,
)
from app.services import tools_service
from scripts.regresion.suite.comun import (
    USUARIO_PRUEBAS_EMAIL,
    TELEFONO_TEST,
    make_payload,
    run_isolated,
)


def p18_caso_1(datos):
    """Consulta permitirse contado: '¿me puedo permitir una tele de 300.000?'"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "¿me puedo permitir una tele de 300.000?"),
            time.perf_counter(),
        )
        resp = respuestas[-1][1] if respuestas else ""

        row = conn.execute(
            text(
                "SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"
            ),
            {"uid": u.id},
        ).mappings().first()
        intent = row["intent_detectado"] if row else None

        db = Session()
        try:
            consulta = detectar_consulta_permitirse("¿me puedo permitir una tele de 300.000?")
            res = tools_service.calcular_puede_permitirse(
                user_id=u.id,
                precio_total=float(consulta["precio"]),
                modo=consulta["modo"],
                cantidad_cuotas=consulta["cantidad_cuotas"],
                tiene_interes=consulta["tiene_interes"],
                tna=float(consulta["tna"]) if consulta.get("tna") is not None else None,
                ingreso_manual=None,
                db=db,
            )
            esperado = armar_respuesta_permitirse(res, consulta)
            resp_ok = (resp == esperado)
        finally:
            db.close()

        return f"Intent: {intent} | Respuesta ok: {resp_ok}"

    return run_isolated(test)


def p18_caso_2(datos):
    """Consulta permitirse cuotas: 'me alcanza para un celular de 600 mil en 6 cuotas?'"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "me alcanza para un celular de 600 mil en 6 cuotas?"),
            time.perf_counter(),
        )
        resp = respuestas[-1][1] if respuestas else ""

        row = conn.execute(
            text(
                "SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"
            ),
            {"uid": u.id},
        ).mappings().first()
        intent = row["intent_detectado"] if row else None

        db = Session()
        try:
            consulta = detectar_consulta_permitirse("me alcanza para un celular de 600 mil en 6 cuotas?")
            res = tools_service.calcular_puede_permitirse(
                user_id=u.id,
                precio_total=float(consulta["precio"]),
                modo=consulta["modo"],
                cantidad_cuotas=consulta["cantidad_cuotas"],
                tiene_interes=consulta["tiene_interes"],
                tna=float(consulta["tna"]) if consulta.get("tna") is not None else None,
                ingreso_manual=None,
                db=db,
            )
            esperado = armar_respuesta_permitirse(res, consulta)
            resp_ok = (resp == esperado)
        finally:
            db.close()

        return f"Intent: {intent} | Respuesta ok: {resp_ok}"

    return run_isolated(test)


def p18_caso_3(datos):
    """Consulta permitirse cuotas de X: '¿puedo comprar una heladera en 12 cuotas de 50 lucas?'"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "¿puedo comprar una heladera en 12 cuotas de 50 lucas?"),
            time.perf_counter(),
        )
        resp = respuestas[-1][1] if respuestas else ""

        row = conn.execute(
            text(
                "SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"
            ),
            {"uid": u.id},
        ).mappings().first()
        intent = row["intent_detectado"] if row else None

        db = Session()
        try:
            consulta = detectar_consulta_permitirse("¿puedo comprar una heladera en 12 cuotas de 50 lucas?")
            res = tools_service.calcular_puede_permitirse(
                user_id=u.id,
                precio_total=float(consulta["precio"]),
                modo=consulta["modo"],
                cantidad_cuotas=consulta["cantidad_cuotas"],
                tiene_interes=consulta["tiene_interes"],
                tna=float(consulta["tna"]) if consulta.get("tna") is not None else None,
                ingreso_manual=None,
                db=db,
            )
            esperado = armar_respuesta_permitirse(res, consulta)
            resp_ok = (resp == esperado)
        finally:
            db.close()

        return f"Intent: {intent} | Respuesta ok: {resp_ok}"

    return run_isolated(test)


def p18_caso_4(datos):
    """Consulta permitirse sin precio: '¿me lo puedo permitir?'"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "¿me lo puedo permitir?"),
            time.perf_counter(),
        )
        resp = respuestas[-1][1] if respuestas else ""

        row = conn.execute(
            text(
                "SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"
            ),
            {"uid": u.id},
        ).mappings().first()
        intent = row["intent_detectado"] if row else None

        esperado = 'Decime cuánto sale. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'
        resp_ok = (resp == esperado)

        return f"Intent: {intent} | Respuesta ok: {resp_ok}"

    return run_isolated(test)
