"""
scripts/regresion/suite/escenarios_p22.py

Escenarios del Punto 22: Gastos en dólares desde cuentas en pesos y deshacer con espejos por WhatsApp.
- P22.1: Gasto USD desde débito ARS - propuesta oficial + percepción y confirmación
- P22.2: Gasto USD desde débito ARS - corrección monto en pesos y confirmación
- P22.3: Gasto USD sin billetera USD - propuesta en pesos y cancelación con no
- P22.4: TF.7 Deshacer compra con reintegro vía WhatsApp
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import time
from uuid import uuid4

from sqlalchemy import select, text

from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
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


GRABACIONES_P22 = {
    "P22.1": {
        "escenario_id": "P22.1",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "gasté 20 dólares en steam con débito de galicia",
                "respuesta": {
                    "intent": "registrar_transaccion",
                    "entidades": {
                        "monto": 20,
                        "moneda": "USD",
                        "tipo": "egreso",
                        "categoria": "Hobbies y juegos",
                        "descripcion": "Steam",
                        "billetera": "Galicia",
                        "billetera_origen": "Galicia",
                        "billetera_destino": None,
                        "cantidad_cuotas": None,
                        "fecha": None,
                        "tarjeta": None,
                        "transacciones_adicionales": [],
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a anotar US$20 en Hobbies y juegos desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P22.2": {
        "escenario_id": "P22.2",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "gasté 20 dólares en steam con débito de galicia",
                "respuesta": {
                    "intent": "registrar_transaccion",
                    "entidades": {
                        "monto": 20,
                        "moneda": "USD",
                        "tipo": "egreso",
                        "categoria": "Hobbies y juegos",
                        "descripcion": "Steam",
                        "billetera": "Galicia",
                        "billetera_origen": "Galicia",
                        "billetera_destino": None,
                        "cantidad_cuotas": None,
                        "fecha": None,
                        "tarjeta": None,
                        "transacciones_adicionales": [],
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a anotar US$20 en Hobbies y juegos desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P22.3": {
        "escenario_id": "P22.3",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "gasté 20 dólares en steam",
                "respuesta": {
                    "intent": "registrar_transaccion",
                    "entidades": {
                        "monto": 20,
                        "moneda": "USD",
                        "tipo": "egreso",
                        "categoria": "Hobbies y juegos",
                        "descripcion": "Steam",
                        "billetera": None,
                        "billetera_origen": None,
                        "billetera_destino": None,
                        "cantidad_cuotas": None,
                        "fecha": None,
                        "tarjeta": None,
                        "transacciones_adicionales": [],
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a anotar US$20 en Hobbies y juegos desde Galicia. ¿Confirmás?",
                },
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
    hoy = hoy_argentina()
    fila = conn.execute(
        text("SELECT id FROM cotizaciones_dolar WHERE fecha = :f AND tipo = 'oficial'"),
        {"f": hoy},
    ).fetchone()
    if fila:
        conn.execute(
            text(
                "UPDATE cotizaciones_dolar SET compra = 950, venta = 1000, promedio = 975, fecha_registro = :fr WHERE id = :id"
            ),
            {"id": fila[0], "fr": datetime.now(timezone.utc)},
        )
    else:
        conn.execute(
            text(
                "INSERT INTO cotizaciones_dolar (id, fecha, tipo, compra, venta, promedio, fecha_registro) "
                "VALUES (:id, :f, 'oficial', 950, 1000, 975, :fr)"
            ),
            {"id": uuid4(), "f": hoy, "fr": datetime.now(timezone.utc)},
        )


def p22_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()
        b_db = db.get(Billetera, b.id)
        s0 = b_db.saldo_actual
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 20 dólares en steam con débito de galicia"), time.perf_counter())
        prop_texto = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        reg_texto = respuestas[-1][1] if respuestas else ""
        primera_linea = reg_texto.strip().split("\n")[0].strip()

        db.expire_all()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        tx = nuevas_txs[0] if len(nuevas_txs) == 1 else None

        mov_ok = (
            len(nuevas_txs) == 1
            and tx is not None
            and tx.moneda in (Moneda.ARS, "ARS")
            and tx.monto == Decimal("26000.00")
            and tx.moneda_original in (Moneda.USD, "USD")
            and tx.monto_original == Decimal("20.00")
            and tx.cotizacion_aplicada == Decimal("1300.0000")
            and tx.billetera_id == b.id
        )
        db.refresh(b_db)
        saldo_ok = (b_db.saldo_actual == s0 - Decimal("26000.00"))
        db.close()

        return (
            f"Propuesta:\n{prop_texto.strip()}\n"
            f"Registro:\n{primera_linea}\n"
            f"Movimiento: {mov_ok} | Saldo: {saldo_ok}"
        )

    return run_isolated(test)


def p22_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()
        b_db = db.get(Billetera, b.id)
        s0 = b_db.saldo_actual
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 20 dólares en steam con débito de galicia"), time.perf_counter())
        prop_texto = respuestas[-1][1] if respuestas else ""
        prop_esperada = (
            "Voy a anotar US$20 en Hobbies y juegos desde Galicia: son $26.000 (oficial $1.000 + 30%).\n"
            "¿Confirmás? Si te cobraron otro monto en pesos, decime cuánto."
        )
        prop_ok = (prop_texto.strip() == prop_esperada)

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "fueron 25.800"), time.perf_counter())
        monto_texto = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        db.expire_all()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        tx = nuevas_txs[0] if len(nuevas_txs) == 1 else None

        mov_ok = (
            len(nuevas_txs) == 1
            and tx is not None
            and tx.moneda in (Moneda.ARS, "ARS")
            and tx.monto == Decimal("25800.00")
            and tx.moneda_original in (Moneda.USD, "USD")
            and tx.monto_original == Decimal("20.00")
            and tx.cotizacion_aplicada == Decimal("1290.0000")
            and tx.billetera_id == b.id
        )
        db.refresh(b_db)
        saldo_ok = (b_db.saldo_actual == s0 - Decimal("25800.00"))
        db.close()

        return (
            f"Propuesta: {prop_ok} | Monto:\n{monto_texto.strip()}\n"
            f"Movimiento: {mov_ok} | Saldo: {saldo_ok}"
        )

    return run_isolated(test)


def p22_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND moneda = 'USD'"), {"uid": u.id})
        tx_ids_antes = set(conn.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 20 dólares en steam"), time.perf_counter())
        prop_texto = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())

        db = Session()
        nuevas_txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.id.not_in(tx_ids_antes))).scalars().all()
        sin_mov = (len(nuevas_txs) == 0)
        db.close()

        return f"Propuesta:\n{prop_texto.strip()}\nSin movimientos: {sin_mov}"

    return run_isolated(test)


def p22_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]

    def test(conn, Session, respuestas):
        _preparar_base_escenario(conn, u.id)
        db = Session()
        b_db = db.get(Billetera, b.id)
        s0_antes_compra = b_db.saldo_actual

        cat_id, sub_id = _resolver_categoria_y_subcategoria("Supermercado", u.id, db, "egreso")
        hoy = hoy_argentina()

        tx_compra = transaccion_service.crear_transaccion(
            db,
            u.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("100000.00"),
                moneda=Moneda.ARS,
                metodo_pago=MetodoPago.DEBITO,
                billetera_id=b.id,
                categoria_id=cat_id,
                subcategoria_id=sub_id,
                fecha=hoy,
                descripcion="Súper",
                origen=OrigenTransaccion.IA_WPP,
            ),
        )
        tx_compra_id = tx_compra.id

        conv = ConversacionWpp(
            usuario_id=u.id,
            mensaje_usuario="gasté 100 mil en el súper",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="bot",
            intent_detectado="registrar_transaccion",
            accion_ejecutada=str(tx_compra_id),
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc),
        )
        db.add(conv)
        db.commit()

        reintegro_service.registrar_o_actualizar_reintegro(
            db,
            u.id,
            tx_compra_id,
            monto=Decimal("25000.00"),
            recibido=True,
            fecha_recibido=hoy,
        )

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        prop_texto = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf_texto = respuestas[-1][1] if respuestas else ""

        db.expire_all()
        compra_check = db.get(Transaccion, tx_compra_id)
        espejos_check = db.execute(
            select(Transaccion).where(Transaccion.reintegro_de_id == tx_compra_id)
        ).scalars().all()
        borrado_ok = (compra_check is None and len(espejos_check) == 0)

        db.refresh(b_db)
        saldo_restaurado = (b_db.saldo_actual == s0_antes_compra)
        db.close()

        return (
            f"Propuesta:\n{prop_texto.strip()}\n"
            f"Confirmación:\n{conf_texto.strip()}\n"
            f"Borrado: {borrado_ok} | Saldo restaurado: {saldo_restaurado}"
        )

    return run_isolated(test)


def entradas_p22(datos) -> list[dict]:
    return [
        {
            "id": "P22.1",
            "punto": "Punto 22",
            "match": "exacto",
            "nombre": "Gasto USD desde débito ARS - propuesta oficial + percepción y confirmación",
            "ejecutar": lambda: p22_caso_1(datos),
            "esperado": (
                "Propuesta:\n"
                "Voy a anotar US$20 en Hobbies y juegos desde Galicia: son $26.000 (oficial $1.000 + 30%).\n"
                "¿Confirmás? Si te cobraron otro monto en pesos, decime cuánto.\n"
                "Registro:\n"
                "Listo. US$20 ($26.000) en Hobbies y juegos desde Galicia — registrado.\n"
                "Movimiento: True | Saldo: True"
            ),
        },
        {
            "id": "P22.2",
            "punto": "Punto 22",
            "match": "exacto",
            "nombre": "Gasto USD desde débito ARS - corrección monto en pesos y confirmación",
            "ejecutar": lambda: p22_caso_2(datos),
            "esperado": (
                "Propuesta: True | Monto:\n"
                "Voy a anotar US$20 en Hobbies y juegos desde Galicia: son $25.800.\n"
                "¿Confirmás?\n"
                "Movimiento: True | Saldo: True"
            ),
        },
        {
            "id": "P22.3",
            "punto": "Punto 22",
            "match": "exacto",
            "nombre": "Gasto USD sin billetera USD - propuesta en pesos y cancelación con no",
            "ejecutar": lambda: p22_caso_3(datos),
            "esperado": (
                "Propuesta:\n"
                "Voy a anotar US$20 en Hobbies y juegos desde Galicia: son $26.000 (oficial $1.000 + 30%).\n"
                "¿Confirmás? Si te cobraron otro monto en pesos, decime cuánto.\n"
                "Sin movimientos: True"
            ),
        },
        {
            "id": "P22.4",
            "punto": "Punto 22",
            "match": "exacto",
            "nombre": "TF.7 Deshacer compra con reintegro vía WhatsApp",
            "ejecutar": lambda: p22_caso_4(datos),
            "esperado": (
                "Propuesta:\n"
                "¿Querés eliminar el último movimiento de $100.000 en Supermercado desde Galicia? También se borra su reintegro de $25.000. ¿Confirmás?\n"
                "Confirmación:\n"
                "Listo, movimiento eliminado.\n"
                "Borrado: True | Saldo restaurado: True"
            ),
        },
    ]
