# -*- coding: utf-8 -*-
"""
scripts/regresion/suite/escenarios_p09c.py

Escenarios P9C (P9C.1 a P9C.6): Transferencias M1 determinísticas con mock de IA.
"""
from __future__ import annotations

import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import func, select, text

from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
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


def p9c_caso_1(datos):
    """P9C.1: transferí 20k a Santander desde Galicia directo determinístico."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE billeteras SET es_principal = (nombre = 'Galicia') "
                "WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, "
                "accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        s = Session()
        txs_ini = s.execute(
            select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)
        ).scalar()
        bg_ini = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Galicia", Billetera.usuario_id == u.id
            )
        ).scalar()
        bs_ini = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Santander", Billetera.usuario_id == u.id
            )
        ).scalar()

        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "transferí 20k a Santander desde Galicia"),
                time.perf_counter(),
            )
        r1 = respuestas[-1][1] if respuestas else ""

        bg_fin = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Galicia", Billetera.usuario_id == u.id
            )
        ).scalar()
        bs_fin = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Santander", Billetera.usuario_id == u.id
            )
        ).scalar()
        txs_fin = s.execute(
            select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)
        ).scalar()

        saldos_ok = (
            bg_fin == bg_ini - Decimal("20000")
            and bs_fin == bs_ini + Decimal("20000")
        )
        cero_gastos = (txs_ini == txs_fin)
        ia = ia_flag[0]
        return (
            f"Respuesta: {r1} | Saldos ajustados: {saldos_ok} | "
            f"Cero gastos: {cero_gastos} | Llegó a la IA: {ia}"
        )

    return run_isolated(test)


def p9c_caso_2(datos):
    """P9C.2: pasé 20 lucas de galicia a macro y 1 (crear cuenta Macro)."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE billeteras SET es_principal = (nombre = 'Galicia') "
                "WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, "
                "accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        s = Session()
        txs_ini = s.execute(
            select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)
        ).scalar()

        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "pasé 20 lucas de galicia a macro"),
                time.perf_counter(),
            )
            r1 = respuestas[-1][1] if respuestas else ""

            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "1"),
                time.perf_counter(),
            )
            r2 = respuestas[-1][1] if respuestas else ""

        b_macro = s.execute(
            select(Billetera).where(
                Billetera.nombre == "Macro", Billetera.usuario_id == u.id
            )
        ).scalar_one_or_none()
        saldo_macro = f"{b_macro.saldo_actual:.2f}" if b_macro else "None"
        txs_fin = s.execute(
            select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)
        ).scalar()
        cero_gastos = (txs_ini == txs_fin)
        ia = ia_flag[0]
        return (
            f"{r1} | {r2} | Macro: {saldo_macro} | Cero gastos: {cero_gastos} | "
            f"Llegó a la IA: {ia}"
        )

    return run_isolated(test)


def p9c_caso_3(datos):
    """P9C.3: pasé 20k de galicia a macro y 3 (pago a otra persona)."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE billeteras SET es_principal = (nombre = 'Galicia') "
                "WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, "
                "accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        s = Session()
        trs_ini = s.execute(
            select(func.count(TransferenciaInterna.id)).where(
                TransferenciaInterna.usuario_id == u.id
            )
        ).scalar()
        txs_ini = s.execute(
            select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)
        ).scalar()

        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "pasé 20k de galicia a macro"),
                time.perf_counter(),
            )
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "3"),
                time.perf_counter(),
            )
            r2 = respuestas[-1][1] if respuestas else ""

        trs_fin = s.execute(
            select(func.count(TransferenciaInterna.id)).where(
                TransferenciaInterna.usuario_id == u.id
            )
        ).scalar()
        txs_fin = s.execute(
            select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)
        ).scalar()

        tr_nuevas = trs_fin - trs_ini
        tx_nuevas = txs_fin - txs_ini
        ia = ia_flag[0]
        return (
            f"{r2} | Transferencias nuevas: {tr_nuevas} | "
            f"Gastos nuevos: {tx_nuevas} | Llegó a la IA: {ia}"
        )

    return run_isolated(test)


def p9c_caso_4(datos):
    """P9C.4: dos transferencias en un mensaje, borrá eso y sí."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE billeteras SET es_principal = (nombre = 'Galicia') "
                "WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, "
                "accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        s = Session()
        bg_ini = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Galicia", Billetera.usuario_id == u.id
            )
        ).scalar()
        bs_ini = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Santander", Billetera.usuario_id == u.id
            )
        ).scalar()
        be_ini = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Efectivo Pesos", Billetera.usuario_id == u.id
            )
        ).scalar()

        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            msg1 = "pasé 20k de galicia a santander y 5k de santander a efectivo"
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, msg1),
                time.perf_counter(),
            )
            r1 = respuestas[-1][1] if respuestas else ""

            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "borrá eso"),
                time.perf_counter(),
            )
            r2 = respuestas[-1][1] if respuestas else ""

            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "sí"),
                time.perf_counter(),
            )
            r3 = respuestas[-1][1] if respuestas else ""

        bg_fin = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Galicia", Billetera.usuario_id == u.id
            )
        ).scalar()
        bs_fin = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Santander", Billetera.usuario_id == u.id
            )
        ).scalar()
        be_fin = s.execute(
            select(Billetera.saldo_actual).where(
                Billetera.nombre == "Efectivo Pesos", Billetera.usuario_id == u.id
            )
        ).scalar()

        saldos_ok = (bg_fin == bg_ini and bs_fin == bs_ini and be_fin == be_ini)
        ia = ia_flag[0]
        return (
            f"{r1} | {r2} | {r3} | Saldos intactos: {saldos_ok} | "
            f"Llegó a la IA: {ia}"
        )

    return run_isolated(test)


def p9c_caso_5(datos):
    """P9C.5: pasé 20k de galicia a santander ayer."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE billeteras SET es_principal = (nombre = 'Galicia') "
                "WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, "
                "accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        s = Session()

        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "pasé 20k de galicia a santander ayer"),
                time.perf_counter(),
            )
            r1 = respuestas[-1][1] if respuestas else ""

        linea1 = r1.splitlines()[0] if r1 else ""
        conv = s.execute(
            select(ConversacionWpp)
            .where(ConversacionWpp.usuario_id == u.id)
            .order_by(ConversacionWpp.fecha.desc())
        ).scalars().first()

        tr = None
        if (
            conv
            and conv.accion_ejecutada
            and conv.accion_ejecutada.startswith("transferencia:")
        ):
            from uuid import UUID
            tr_id = UUID(conv.accion_ejecutada.replace("transferencia:", ""))
            tr = s.get(TransferenciaInterna, tr_id)
        if not tr:
            tr = s.execute(
                select(TransferenciaInterna)
                .where(TransferenciaInterna.usuario_id == u.id)
                .order_by(TransferenciaInterna.fecha_creacion.desc())
            ).scalars().first()

        fecha_ayer = bool(
            tr and tr.fecha == (hoy_argentina() - timedelta(days=1))
        )
        ia = ia_flag[0]
        return f"{linea1} | Fecha ayer: {fecha_ayer} | Llegó a la IA: {ia}"

    return run_isolated(test)


def p9c_caso_6(datos):
    """P9C.6: pasé 100 dólares a galicia (pregunta cotización)."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        conn.execute(
            text(
                "UPDATE billeteras SET es_principal = (nombre = 'Galicia') "
                "WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )
        conn.execute(
            text(
                "UPDATE conversaciones_wpp SET slot_filling_activo = false, "
                "accion_ejecutada = 'test' WHERE usuario_id = :uid"
            ),
            {"uid": u.id},
        )

        ia_flag, mock_ia = _crear_mock_ia()
        respuestas.clear()
        with patch.object(ai_service, "procesar_mensaje", side_effect=mock_ia):
            _procesar_webhook_whatsapp_sync(
                make_payload(TELEFONO_TEST, "pasé 100 dólares a galicia"),
                time.perf_counter(),
            )
            r1 = respuestas[-1][1] if respuestas else ""

        ia = ia_flag[0]
        return f"{r1} | Llegó a la IA: {ia}"

    return run_isolated(test)
