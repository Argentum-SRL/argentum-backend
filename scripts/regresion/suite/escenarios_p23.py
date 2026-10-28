"""
scripts/regresion/suite/escenarios_p23.py

Escenarios del Punto 23: Reintegros y ¿Qué es esta plata? por WhatsApp (fase_f2b_wpp_reintegros).
- P23.1: Reintegro directo por WhatsApp de compra previa en pesos
- P23.2: Deshacer reintegro recibido directo por WhatsApp
- P23.3: Confirmar reintegro pendiente previo y deshacerlo por WhatsApp
- P23.4: Aviso ¿Qué era? tras cobro de persona y vinculación como parte de gasto
- P23.5: Aviso ¿Qué era? tras cobro de persona y pase a 'Pasó por mi cuenta'
"""
from __future__ import annotations

from decimal import Decimal
import time

from sqlalchemy import select, text

from app.models.billetera import Billetera
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.usuario import Moneda
from app.routers.whatsapp.db_lookups import _resolver_categoria_y_subcategoria
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.schemas.transaccion import TransaccionCreate
from app.services import reintegro_service, transaccion_service
from app.utils.fecha import hoy_argentina
from scripts.regresion.suite.comun import (
    TELEFONO_TEST,
    USUARIO_PRUEBAS_EMAIL,
    make_payload,
    run_isolated,
)

# Grabación de P17.13 copiada sin cambios para P23.4 y P23.5 (Excepción a)
_P17_13_RESPUESTA = {
    "intent": "registrar_transaccion",
    "entidades": {
        "monto": 3000,
        "moneda": "ARS",
        "tipo": "ingreso",
        "categoria": None,
        "descripcion": "Transferencia de juan",
        "servicio": None,
        "frecuencia": None,
        "billetera": None,
        "billetera_origen": None,
        "billetera_destino": None,
        "cantidad_cuotas": None,
        "fecha": None,
        "destinatario": None,
        "persona": None,
        "tasa_cambio": None,
        "monto_destino": None,
        "monto_comision": None,
        "confirmado": None,
        "tarjeta": None,
        "meta": None,
        "transacciones_adicionales": [],
    },
    "confianza": 0.9,
    "slot_filling": False,
    "datos_faltantes": [],
    "respuesta_usuario": "Listo. $3.000 de ingreso registrado como transferencia de Juan.",
}

GRABACIONES_P23 = {
    "P23.4": {
        "escenario_id": "P23.4",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "Juan me transfirió 3000",
                "respuesta": _P17_13_RESPUESTA,
            }
        ],
    },
    "P23.5": {
        "escenario_id": "P23.5",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "Juan me transfirió 3000",
                "respuesta": _P17_13_RESPUESTA,
            }
        ],
    },
}


def _preparar_base_escenario(conn, uid):
    conn.execute(
        text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"),
        {"uid": uid},
    )
    conn.execute(
        text(
            "UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"
        ),
        {"uid": uid},
    )


def p23_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Supermercado", u.id, db, "egreso")
        tx_compra = transaccion_service.crear_transaccion(
            db,
            u.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("100000.00"),
                moneda=Moneda.ARS,
                metodo_pago=MetodoPago.DEBITO,
                categoria_id=cat_id,
                subcategoria_id=sub_id,
                billetera_id=b.id,
                fecha=hoy_argentina(),
                descripcion="Kuma",
                origen=OrigenTransaccion.MANUAL,
            ),
        )
        b_db = db.get(Billetera, b.id)
        s_despues_compra = b_db.saldo_actual

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "me devolvieron 25000 de kuma"),
            time.perf_counter(),
        )
        resp_texto = respuestas[-1][1] if respuestas else ""

        db.expire_all()
        espejos = db.execute(
            select(Transaccion).where(
                Transaccion.reintegro_de_id == tx_compra.id,
                Transaccion.tipo_espejo == "reintegro",
                Transaccion.es_cuota_hija.is_(False),
            )
        ).scalars().all()
        espejo_ok = (
            len(espejos) == 1
            and espejos[0].monto == Decimal("-25000.00")
            and espejos[0].fecha == hoy_argentina()
            and espejos[0].billetera_id == b.id
        )
        db.refresh(b_db)
        saldo_ok = (b_db.saldo_actual == s_despues_compra + Decimal("25000.00"))
        db.close()

        return (
            f"Respuesta:\n{resp_texto.strip()}\n"
            f"Espejo: {espejo_ok} | Saldo: {saldo_ok}"
        )

    return run_isolated(test)


def p23_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Supermercado", u.id, db, "egreso")
        tx_compra = transaccion_service.crear_transaccion(
            db,
            u.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("100000.00"),
                moneda=Moneda.ARS,
                metodo_pago=MetodoPago.DEBITO,
                categoria_id=cat_id,
                subcategoria_id=sub_id,
                billetera_id=b.id,
                fecha=hoy_argentina(),
                descripcion="Kuma",
                origen=OrigenTransaccion.MANUAL,
            ),
        )
        b_db = db.get(Billetera, b.id)
        s_despues_compra = b_db.saldo_actual

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "me devolvieron 25000 de kuma"),
            time.perf_counter(),
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "borrá eso"),
            time.perf_counter(),
        )
        prop_texto = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "sí"),
            time.perf_counter(),
        )
        conf_texto = respuestas[-1][1] if respuestas else ""

        db.expire_all()
        espejos = db.execute(
            select(Transaccion).where(Transaccion.reintegro_de_id == tx_compra.id)
        ).scalars().all()
        compra_actualizada = db.get(Transaccion, tx_compra.id)
        sin_reintegro = (len(espejos) == 0 and compra_actualizada.reintegro_esperado is None)
        db.refresh(b_db)
        saldo_ok = (b_db.saldo_actual == s_despues_compra)
        db.close()

        return (
            f"Propuesta:\n{prop_texto.strip()}\n"
            f"Confirmación:\n{conf_texto.strip()}\n"
            f"Sin reintegro: {sin_reintegro} | Saldo: {saldo_ok}"
        )

    return run_isolated(test)


def p23_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Supermercado", u.id, db, "egreso")
        tx_compra = transaccion_service.crear_transaccion(
            db,
            u.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("100000.00"),
                moneda=Moneda.ARS,
                metodo_pago=MetodoPago.DEBITO,
                categoria_id=cat_id,
                subcategoria_id=sub_id,
                billetera_id=b.id,
                fecha=hoy_argentina(),
                descripcion="Kuma",
                origen=OrigenTransaccion.MANUAL,
            ),
        )
        reintegro_service.registrar_o_actualizar_reintegro(
            db, u.id, tx_compra.id, monto=Decimal("25000.00"), recibido=False
        )
        b_db = db.get(Billetera, b.id)
        s_despues_compra = b_db.saldo_actual

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "me llegó el reintegro de kuma"),
            time.perf_counter(),
        )
        reg_texto = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "borrá eso"),
            time.perf_counter(),
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "sí"),
            time.perf_counter(),
        )
        conf_texto = respuestas[-1][1] if respuestas else ""

        db.expire_all()
        espejos = db.execute(
            select(Transaccion).where(Transaccion.reintegro_de_id == tx_compra.id)
        ).scalars().all()
        compra_actualizada = db.get(Transaccion, tx_compra.id)
        pendiente_ok = (len(espejos) == 0 and compra_actualizada.reintegro_esperado == Decimal("25000.00"))
        db.refresh(b_db)
        saldo_ok = (b_db.saldo_actual == s_despues_compra)
        db.close()

        return (
            f"Registro:\n{reg_texto.strip()}\n"
            f"Confirmación:\n{conf_texto.strip()}\n"
            f"Pendiente: {pendiente_ok} | Saldo: {saldo_ok}"
        )

    return run_isolated(test)


def p23_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Salidas", u.id, db, "egreso")
        tx_asado = transaccion_service.crear_transaccion(
            db,
            u.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("80000.00"),
                moneda=Moneda.ARS,
                metodo_pago=MetodoPago.DEBITO,
                categoria_id=cat_id,
                subcategoria_id=sub_id,
                billetera_id=b.id,
                fecha=hoy_argentina(),
                descripcion="Asado",
                origen=OrigenTransaccion.MANUAL,
            ),
        )
        b_db = db.get(Billetera, b.id)
        s_antes_cobro = b_db.saldo_actual

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "Juan me transfirió 3000"),
            time.perf_counter(),
        )
        reg_texto = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "2"),
            time.perf_counter(),
        )
        lista_texto = respuestas[-1][1] if respuestas else ""
        lista_ok = lista_texto.startswith("¿De qué gasto?\n1) Asado $80.000")

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "1"),
            time.perf_counter(),
        )
        resp_texto = respuestas[-1][1] if respuestas else ""

        db.expire_all()
        cobros = db.execute(
            select(Transaccion).where(
                Transaccion.usuario_id == u.id,
                Transaccion.reintegro_de_id == tx_asado.id,
                Transaccion.tipo_espejo == "parte",
            )
        ).scalars().all()
        parte_ok = (len(cobros) == 1 and cobros[0].monto == Decimal("-3000.00"))
        db.refresh(b_db)
        saldo_ok = (b_db.saldo_actual == s_antes_cobro + Decimal("3000.00"))
        db.close()

        return (
            f"Registro:\n{reg_texto.strip()}\n"
            f"Lista: {lista_ok}\n"
            f"Respuesta:\n{resp_texto.strip()}\n"
            f"Parte: {parte_ok} | Saldo: {saldo_ok}"
        )

    return run_isolated(test)


def p23_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "Juan me transfirió 3000"),
            time.perf_counter(),
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "3"),
            time.perf_counter(),
        )
        resp_texto = respuestas[-1][1] if respuestas else ""

        db.expire_all()
        tx = db.execute(
            select(Transaccion)
            .where(
                Transaccion.usuario_id == u.id,
                Transaccion.origen == OrigenTransaccion.IA_WPP,
            )
            .order_by(Transaccion.fecha_creacion.desc(), Transaccion.id.desc())
        ).scalars().first()
        paso_ok = (
            tx is not None
            and tx.subcategoria is not None
            and tx.subcategoria.nombre == "Pasó por mi cuenta"
        )
        db.close()

        return (
            f"Respuesta:\n{resp_texto.strip()}\n"
            f"Pasó por mi cuenta: {paso_ok}"
        )

    return run_isolated(test)


def entradas_p23(datos) -> list[dict]:
    return [
        {
            "id": "P23.1",
            "punto": "Punto 23",
            "match": "exacto",
            "nombre": "Reintegro directo por WhatsApp de compra previa en pesos",
            "ejecutar": lambda: p23_caso_1(datos),
            "esperado": (
                "Respuesta:\n"
                "Listo, anoté la devolución de $25.000 de «Kuma». Si no era esa, decime «borrá eso».\n"
                "Espejo: True | Saldo: True"
            ),
        },
        {
            "id": "P23.2",
            "punto": "Punto 23",
            "match": "exacto",
            "nombre": "Deshacer reintegro recibido directo por WhatsApp",
            "ejecutar": lambda: p23_caso_2(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Querés eliminar la devolución de $25.000 de «Kuma» en Galicia? ¿Confirmás?\n"
                "Confirmación:\n"
                "Listo, devolución eliminada.\n"
                "Sin reintegro: True | Saldo: True"
            ),
        },
        {
            "id": "P23.3",
            "punto": "Punto 23",
            "match": "exacto",
            "nombre": "Confirmar reintegro pendiente previo y deshacerlo por WhatsApp",
            "ejecutar": lambda: p23_caso_3(datos),
            "esperado": (
                "Registro:\n"
                "Listo, anoté la devolución de $25.000 de «Kuma». Si no era esa, decime «borrá eso».\n"
                "Confirmación:\n"
                "Listo, devolución eliminada.\n"
                "Pendiente: True | Saldo: True"
            ),
        },
        {
            "id": "P23.4",
            "punto": "Punto 23",
            "match": "exacto",
            "nombre": "Aviso ¿Qué era? tras cobro de persona y vinculación como parte de gasto",
            "ejecutar": lambda: p23_caso_4(datos),
            "esperado": (
                "Registro:\n"
                "Listo. Ingreso de $3.000 en Cobros de personas a Galicia — registrado.\n"
                "Si fue con otra, decime cuál.\n"
                "¿Qué era? 1) Me pagaron 2) Me devolvieron parte de un gasto 3) Solo pasó por mi cuenta\n"
                "Lista: True\n"
                "Respuesta:\n"
                "Listo, los $3.000 se restan de «Asado».\n"
                "Parte: True | Saldo: True"
            ),
        },
        {
            "id": "P23.5",
            "punto": "Punto 23",
            "match": "exacto",
            "nombre": "Aviso ¿Qué era? tras cobro de persona y pase a 'Pasó por mi cuenta'",
            "ejecutar": lambda: p23_caso_5(datos),
            "esperado": (
                "Respuesta:\n"
                "Listo, queda como plata que solo pasó por tu cuenta: no cuenta como ingreso.\n"
                "Pasó por mi cuenta: True"
            ),
        },
    ]
