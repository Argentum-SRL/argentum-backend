# -*- coding: utf-8 -*-
"""
scripts/regresion/suite/escenarios_p18c.py

Escenarios P18C (P18C.1 y P18C.2): Bloque T2 Cuotas vs contado y precio de contado en WhatsApp.
"""
from __future__ import annotations

from decimal import Decimal
import time
from unittest.mock import patch

from sqlalchemy import select, text

from app.models.conversacion_wpp import ConversacionWpp
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.services import ai_service
from app.utils.fecha import hoy_argentina
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


def p18c_caso_1(datos):
    """P18C.1: ¿me conviene 500 mil contado o 12 de 50 mil? determinístico sin llegar a la IA."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_wpp(conn, u.id)
        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "¿me conviene 500 mil contado o 12 de 50 mil?"),
                time.perf_counter(),
            )
        r1 = respuestas[-1][1] if respuestas else ""
        primera = r1.split("\n")[0] if r1 else ""
        s = Session()
        intent = _ultima_intent(s, u.id)
        if ia_flag[0]:
            raise AssertionError("P18C.1 llegó a la IA cuando debía ser determinístico")
        return f"Intent: {intent} | Primera línea: {primera} | Llegó a la IA: {ia_flag[0]}"

    return run_isolated(test)


def p18c_caso_2(datos):
    """P18C.2: compra en cuotas con contado (IA fija devolviendo compra en cuotas)."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_wpp(conn, u.id)
        s = Session()
        tarjeta = s.execute(
            select(TarjetaCredito).where(
                TarjetaCredito.usuario_id == u.id,
                TarjetaCredito.estado == EstadoTarjeta.ACTIVA,
            )
        ).scalars().first()
        nombre_tarjeta = tarjeta.nombre if tarjeta else "Visa Galicia"
        grupos_antes = {
            g.id
            for g in s.execute(
                select(GrupoCuotas).where(GrupoCuotas.usuario_id == u.id)
            ).scalars().all()
        }

        def _mock_compra_cuotas(*args, **kwargs):
            return {
                "intent": "registrar_transaccion",
                "entidades": {
                    "tipo": "egreso",
                    "monto": 960000,
                    "categoria": "Tecnología",
                    "descripcion": "Heladera",
                    "metodo_pago": "credito",
                    "tarjeta": nombre_tarjeta,
                    "cantidad_cuotas": 12,
                    "fecha": str(hoy_argentina()),
                },
                "confianza": 0.95,
                "slot_filling": False,
                "datos_faltantes": [],
                "respuesta_usuario": "",
            }

        respuestas.clear()
        msg = (
            f"compré una heladera con {nombre_tarjeta} "
            "en 12 cuotas de 80 mil, de contado 800 mil"
        )
        with patch.object(ai_service, "procesar_mensaje", side_effect=_mock_compra_cuotas):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, msg),
                time.perf_counter(),
            )
        s.expire_all()
        nuevos = [
            g
            for g in s.execute(
                select(GrupoCuotas).where(GrupoCuotas.usuario_id == u.id)
            ).scalars().all()
            if g.id not in grupos_antes
        ]
        if not nuevos:
            with patch.object(ai_service, "procesar_mensaje", side_effect=_mock_compra_cuotas):
                _procesar_webhook_whatsapp_sync(
                    make_payload(TELEFONO_TEST, "sí"),
                    time.perf_counter(),
                )
            s.expire_all()
            nuevos = [
                g
                for g in s.execute(
                    select(GrupoCuotas).where(GrupoCuotas.usuario_id == u.id)
                ).scalars().all()
                if g.id not in grupos_antes
            ]

        guardado = bool(nuevos and nuevos[0].precio_contado == Decimal("800000.00"))
        return f"Precio de contado guardado: {guardado}"

    return run_isolated(test)
