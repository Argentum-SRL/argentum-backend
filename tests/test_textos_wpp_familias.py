# -*- coding: utf-8 -*-
"""Pruebas por familia (D1 a D10, D16, DT.5 y caso DT.6.6)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
import pytest

from app.models.billetera import Billetera, EstadoBilletera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.deshacer_corregir import (
    _confirmar_propuesta_corregir,
    _construir_propuesta_corregir,
    _construir_propuesta_deshacer,
)
from app.routers.whatsapp.dolares_desde_pesos import texto_propuesta_usd_pesos
from app.routers.whatsapp.etapa_handlers import procesar_estados_y_handlers_deterministicos
from app.routers.whatsapp.etapa_resolucion import (
    _resolver_billetera_y_duplicados,
    _resolver_tarjeta_y_cuotas,
    procesar_resolucion_billetera_y_propuestas,
)
from app.routers.whatsapp.factura_wpp import (
    manejar_factura_no_pagada,
    texto_propuesta_factura,
)
from app.routers.whatsapp.handlers_confirmaciones import (
    manejar_cancelacion,
    manejar_confirmacion,
)
from app.routers.whatsapp.handlers_deshacer import manejar_deshacer
from app.routers.whatsapp.handlers_menus import manejar_menu_billetera
from app.routers.whatsapp.handlers_transferencias import manejar_pago_resumen
from app.routers.whatsapp.lote_documento import (
    armar_resultado_ia_documento,
    formatear_lineas_omitidos,
)
from app.routers.whatsapp.memoria_comercio_wpp import (
    confirmar_memoria,
    preguntar_memoria_tras_correccion,
)
from app.routers.whatsapp.propuestas import (
    _construir_propuesta_credito,
    _construir_propuesta_transaccion,
)
from app.routers.whatsapp.registro import (
    _confirmar_propuesta_transaccion,
    _formatear_confirmacion_lote_unificada,
    _registrar_movimiento_directo,
)
from app.routers.whatsapp.resolvers_cascada import (
    _generar_menu_billeteras,
    _generar_menu_tarjetas,
)
from app.services import calendario_tarjeta_service, suscripcion_service
from app.utils.fecha import TZ_ARGENTINA
from tests.wpp_familias_helpers import (
    _crear_conv_propuesta,
    _crear_escenario_base,
    _fijar_hoy,
    aplicar_compat_sqlite,
    crear_db_sqlite,
    instalar_enviados,
)


@pytest.fixture(autouse=True)
def _compat_sqlite(monkeypatch):
    """Aplica compatibilidad UUID/DateTime para SQLite."""
    aplicar_compat_sqlite(monkeypatch)


@pytest.fixture(name="db", scope="function")
def db_fixture(monkeypatch):
    """Sesión SQLite en memoria por test."""
    session = crear_db_sqlite(monkeypatch)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="enviados")
def enviados_fixture(monkeypatch):
    """Lista de mensajes interceptados de enviar_whatsapp."""
    return instalar_enviados(monkeypatch)


def test_d1_registro_gasto_y_negativo(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    conv = _crear_conv_propuesta(
        db,
        u,
        "registrar_transaccion",
        {
            "monto": 5000,
            "tipo": "egreso",
            "moneda": "ARS",
            "categoria": "Alimentación > Kiosco",
            "billetera_origen": "Galicia",
            "descripcion": "Kiosco",
            "fecha": hoy.isoformat(),
        },
    )
    _, msg, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=conv.id)
    assert msg == "Listo, anoté $5.000 en Kiosco desde Galicia."

    # También el registro directo debe usar D1 exacto sin "Si fue con otra, decime cuál."
    _, msg_dir = _registrar_movimiento_directo(
        u,
        {
            "monto": 5000,
            "tipo": "egreso",
            "moneda": "ARS",
            "categoria": "Alimentación > Kiosco",
            "billetera_origen": "Galicia",
            "descripcion": "Kiosco",
            "fecha": hoy.isoformat(),
        },
        db,
    )
    assert msg_dir == "Listo, anoté $5.000 en Kiosco desde Galicia."

    conv2 = _crear_conv_propuesta(
        db,
        u,
        "registrar_transaccion",
        {
            "monto": 120000,
            "tipo": "egreso",
            "moneda": "ARS",
            "categoria": "Alimentación > Supermercado",
            "billetera_origen": "Galicia",
            "descripcion": "Supermercado",
            "fecha": date(2026, 11, 15).isoformat(),
        },
    )
    _, msg_neg, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=conv2.id)
    assert msg_neg == (
        "Listo, anoté $120.000 en Supermercado desde Galicia (ayer).\n"
        "Galicia quedó en negativo."
    )


def test_d2_registro_ingreso_hoy_y_fecha(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    c1 = _crear_conv_propuesta(
        db,
        u,
        "registrar_transaccion",
        {
            "monto": 500000,
            "tipo": "ingreso",
            "moneda": "ARS",
            "categoria": "Ingresos > Sueldo",
            "billetera_destino": "Galicia",
            "descripcion": "Sueldo",
            "fecha": hoy.isoformat(),
        },
    )
    _, msg1, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=c1.id)
    assert msg1 == "Listo, anoté un ingreso de $500.000 en Galicia (Sueldo)."

    c2 = _crear_conv_propuesta(
        db,
        u,
        "registrar_transaccion",
        {
            "monto": 20000,
            "tipo": "ingreso",
            "moneda": "ARS",
            "categoria": "Ingresos > Sueldo",
            "billetera_destino": "Galicia",
            "descripcion": "Bono",
            "fecha": date(2026, 10, 9).isoformat(),
        },
    )
    _, msg2, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=c2.id)
    assert msg2 == "Listo, anoté un ingreso de $20.000 en Galicia (Sueldo, 9/10)."


def test_d3_varios_misma_y_varias_cuentas(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]
    b_g = datos["galicia"]
    b_mp = datos["mp"]
    tarjeta = datos["tarjeta"]

    tx1 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_g.id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_kiosco"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.DEBITO,
        descripcion="Kiosco",
        origen=OrigenTransaccion.IA_WPP,
    )
    tx2 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_g.id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_super"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 15),
        metodo_pago=MetodoPago.DEBITO,
        descripcion="Super",
        origen=OrigenTransaccion.IA_WPP,
    )
    db.add_all([tx1, tx2])
    db.flush()
    msg_misma = _formatear_confirmacion_lote_unificada([tx1, tx2], u.id, db)
    assert msg_misma == (
        "Listo, anoté 2 gastos desde Galicia:\n"
        "- $2.000 en Kiosco\n"
        "- $5.000 en Supermercado · ayer"
    )
    assert "\n\n" not in msg_misma
    assert "Registrados." not in msg_misma

    tx3 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_mp.id,
        categoria_id=datos["cat_ing"].id,
        subcategoria_id=datos["sub_sueldo"].id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("30000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        descripcion="Sueldo",
        origen=OrigenTransaccion.IA_WPP,
    )
    tx4 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=tarjeta.billetera_id,
        tarjeta_id=tarjeta.id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_deliv"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.CREDITO,
        descripcion="Pizza",
        origen=OrigenTransaccion.IA_WPP,
    )
    db.add_all([tx3, tx4])
    db.flush()
    msg_mezcla = _formatear_confirmacion_lote_unificada([tx1, tx3, tx4], u.id, db)
    assert msg_mezcla == (
        "Listo, anoté 3 movimientos:\n"
        "- $2.000 en Kiosco · Galicia\n"
        "- $30.000 en Sueldo (ingreso) · Mercado Pago\n"
        "- $10.000 en Delivery, en un pago · Visa Galicia •••• 1506"
    )


def test_d4_ya_anotado_cancelado_vencido(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    _, msg_nada, _ = _confirmar_propuesta_transaccion(u, db)
    assert msg_nada == "No tengo nada pendiente para confirmar."

    conv_dir = ConversacionWpp(
        usuario_id=u.id,
        mensaje_usuario="gasté 5000",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="Listo, anoté $5.000 en Kiosco desde Galicia.",
        intent_detectado="registrar_transaccion",
        entidades={"registro_directo": True},
        accion_ejecutada=str(uuid4()),
        confianza=Decimal("1.000"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv_dir)
    db.commit()
    assert manejar_confirmacion("sí", u, db, u.telefono) is True
    assert enviados[-1] == "Ya lo había anotado."

    assert manejar_cancelacion("cancelar", u, db, u.telefono) is True
    assert enviados[-1] == "Listo, cancelado."


def test_d5_tarjeta_credito(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]
    tarjeta = datos["tarjeta"]

    _, venc = calendario_tarjeta_service.resumen_de_compra(tarjeta, hoy, proximo_resumen=False)
    v_str = venc.strftime("%d/%m")
    prop_cuotas = _construir_propuesta_credito(
        {"categoria": "Alimentación > Supermercado"},
        tarjeta,
        3,
        Decimal("6666.67"),
        Decimal("20000"),
        venc,
        db=db,
        usuario_id=u.id,
    )
    assert prop_cuotas == (
        "3 cuotas de $6.666,67 en Supermercado con la Visa Galicia •••• 1506 ($20.000).\n"
        f"La primera vence el {v_str}. ¿Las anoto?"
    )

    prop_1 = _construir_propuesta_credito(
        {"categoria": "Alimentación > Kiosco"},
        tarjeta,
        1,
        Decimal("5000"),
        Decimal("5000"),
        venc,
        db=db,
        usuario_id=u.id,
    )
    assert prop_1 == (
        "$5.000 en Kiosco con la Visa Galicia •••• 1506, en un pago.\n"
        f"Vence el {v_str}. ¿Lo anoto?"
    )

    menu_t = _generar_menu_tarjetas([tarjeta])
    assert menu_t == "¿Con qué tarjeta fue?\n1. Visa Galicia •••• 1506"

    assert manejar_pago_resumen("pagar el resumen", u, db, u.telefono) is True
    assert enviados[-1] == "El pago del resumen se carga en miargentum.com, no por acá."

    ctx_sin = ContextoMensaje(
        datos_mensaje={},
        msg={},
        wamid="w1",
        from_number=u.telefono,
        msg_type="text",
        t_inicio=0.0,
        usuario=u,
        db=db,
        mensaje_texto="compré con tarjeta 5000",
        resultado_ia={"intent": "registrar_transaccion", "entidades": {"monto": 5000}},
        confianza_ia_raw=0.95,
    )
    _resolver_tarjeta_y_cuotas(
        ctx_sin, ctx_sin.resultado_ia["entidades"], [], "compre", True, None, []
    )
    assert ctx_sin.resultado_ia["respuesta_usuario"] == (
        "No tenés tarjetas de crédito cargadas.\n"
        "Agregala en miargentum.com o decime de qué cuenta salió y lo anoto como gasto."
    )


def test_d6_propuestas_y_usd_pesos(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    prop_g = _construir_propuesta_transaccion(
        {
            "monto": 5000,
            "tipo": "egreso",
            "moneda": "ARS",
            "categoria": "Alimentación > Kiosco",
            "billetera": "Galicia",
        },
        billetera_nombre="Galicia",
        db=db,
        usuario_id=u.id,
    )
    assert prop_g == "¿Anoto $5.000 en Kiosco desde Galicia?"

    prop_usd = texto_propuesta_usd_pesos(
        {
            "monto_original": 10,
            "monto": 15000,
            "cotizacion_sugerida": 1150,
            "percepcion": 30,
            "categoria": "Servicios > Electricidad",
        },
        "Galicia",
    )
    assert prop_usd == (
        "US$10 en Electricidad desde Galicia: son $15.000 (oficial $1.150 + 30%).\n"
        "¿Lo anoto? Si te cobraron otro monto en pesos, decime cuánto."
    )

    prop_sin_cot = texto_propuesta_usd_pesos(
        {"monto_original": 10, "monto": None, "categoria": "Servicios > Electricidad"},
        "Galicia",
    )
    assert prop_sin_cot == "US$10 en Electricidad desde Galicia. ¿Cuántos pesos te cobraron?"


def test_d7_preguntas_cuenta_y_colision(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    menu = _generar_menu_billeteras([datos["galicia"], datos["mp"]], tipo="egreso")
    assert menu == "¿De qué cuenta salió?\n1. Galicia\n2. Mercado Pago"

    conv_sf = _crear_conv_propuesta(
        db,
        u,
        "slot_filling",
        {},
        slot_activo=True,
        slot_estado={
            "monto": 5000,
            "tipo": "egreso",
            "moneda": "ARS",
            "datos_faltantes": ["billetera_origen"],
        },
    )
    assert manejar_menu_billetera("9", u, db, u.telefono, conv_activa=conv_sf) is True
    assert enviados[-1] == "Ese número no está en la lista. Elegí del 1 al 2."

    # Colisión cuenta o tarjeta (D7)
    datos["tarjeta"].nombre = "Galicia"
    db.commit()
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={},
        wamid="w_col",
        from_number=u.telefono,
        msg_type="text",
        t_inicio=0.0,
        usuario=u,
        db=db,
        mensaje_texto="gasté 5000 con galicia",
        resultado_ia={
            "intent": "registrar_transaccion",
            "entidades": {"monto": 5000, "tipo": "egreso"},
        },
        confianza_ia_raw=0.95,
    )
    procesar_resolucion_billetera_y_propuestas(ctx)
    assert ctx.resultado_ia["respuesta_usuario"] == (
        "¿Fue con la cuenta o con la tarjeta?\n"
        "1. Cuenta Galicia\n"
        "2. Tarjeta Visa Galicia"
    )


def test_d8_duplicados_y_suscripcion_cobrada(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    hace_5 = datetime.now(timezone.utc) - timedelta(minutes=5)
    tx_prev = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=datos["galicia"].id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_kiosco"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.DEBITO,
        descripcion="Kiosco",
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=hace_5,
    )
    db.add(tx_prev)
    db.commit()

    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={},
        wamid="w_dup",
        from_number=u.telefono,
        msg_type="text",
        t_inicio=0.0,
        usuario=u,
        db=db,
        mensaje_texto="gasté 5000 en kiosco con galicia",
        resultado_ia={"intent": "registrar_transaccion", "entidades": {}},
        confianza_ia_raw=0.95,
    )
    ents = {
        "monto": 5000,
        "tipo": "egreso",
        "moneda": "ARS",
        "categoria": "Kiosco",
        "billetera_origen": "Galicia",
        "fecha": hoy.isoformat(),
    }
    _resolver_billetera_y_duplicados(
        ctx, ents, [datos["galicia"]], "billetera_origen", "billetera_destino", "egreso"
    )
    hhmm = hace_5.astimezone(TZ_ARGENTINA).strftime("%H:%M")
    assert ctx.resultado_ia["respuesta_usuario"] == (
        f"A las {hhmm} ya anotaste $5.000 en Kiosco. ¿Lo anoto otra vez?"
    )


def test_d9_borrar_con_espejos_y_errores(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    hace_2h = datetime.now(timezone.utc) - timedelta(hours=2)
    tx_compra = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=datos["galicia"].id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_super"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.DEBITO,
        descripcion="Super",
        origen=OrigenTransaccion.IA_WPP,
        fecha_creacion=hace_2h,
    )
    db.add(tx_compra)
    db.flush()
    tx_reint = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=datos["galicia"].id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_super"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("-2500"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.DEBITO,
        descripcion="Reintegro",
        origen=OrigenTransaccion.IA_WPP,
        reintegro_de_id=tx_compra.id,
        tipo_espejo="reintegro",
        fecha_creacion=hace_2h,
    )
    db.add(tx_reint)
    db.commit()

    prop = _construir_propuesta_deshacer(tx_compra, db)
    assert prop == (
        "Si lo borro, también se borra su reintegro de $2.500.\n"
        "¿Borro $10.000 en Supermercado desde Galicia?"
    )
    assert manejar_deshacer("borrá eso", u, db, u.telefono) is True
    assert enviados[-1] == "Pasaron más de 30 minutos. Borralo en miargentum.com"


def test_d10_corregir_y_memoria(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    tx = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=datos["galicia"].id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_kiosco"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.DEBITO,
        descripcion="kiosco pepe",
        origen=OrigenTransaccion.IA_WPP,
    )
    db.add(tx)
    db.commit()

    p1 = _construir_propuesta_corregir(tx, {"monto": 3000}, db)
    assert p1 == "¿Lo cambio de $30.000 a $3.000?"
    assert "Antes:" not in p1 and "Ahora:" not in p1

    cambios_2 = {
        "monto": 3000,
        "categoria_id": str(datos["cat_alim"].id),
        "subcategoria_id": str(datos["sub_super"].id),
        "categoria_nombre": "Supermercado",
    }
    p2 = _construir_propuesta_corregir(tx, cambios_2, db)
    assert p2 == "¿Lo dejo en $3.000 en Supermercado desde Galicia?"

    conv_c = _crear_conv_propuesta(
        db, u, "corregir", {"transaccion_id": str(tx.id), "cambios": cambios_2}
    )
    _, msg_ok, _ = _confirmar_propuesta_corregir(u, db)
    assert msg_ok == "Listo, quedó $3.000 en Supermercado desde Galicia."
    assert conv_c.id is not None

    preguntar_memoria_tras_correccion(u, db, u.telefono, tx, cambios_2)
    assert enviados[-1] == "¿Siempre que digas _kiosco pepe_ lo pongo ahí?"

    conv_mem = db.execute(
        select(ConversacionWpp).where(ConversacionWpp.intent_detectado == "memoria_comercio")
    ).scalar_one()
    confirmar_memoria(u, db, u.telefono, conv_mem)
    assert enviados[-1] == "Listo, lo voy a recordar."


def test_d16_facturas_fotos_y_p19_7(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    # P19.7: propuesta de factura con vencimiento (sin categoría distinta a confirmación)
    venc = date(2026, 11, 26)
    entidades_fac = {
        "monto": 14200,
        "tipo": "egreso",
        "moneda": "ARS",
        "categoria": "Electricidad",
        "descripcion": "Edesur",
        "billetera_origen": "Galicia",
        "fecha": hoy.isoformat(),
        "factura": {
            "empresa": "Edesur",
            "origen": "whatsapp_foto",
            "vencimientos": [{"fecha": venc.isoformat(), "monto": "14200"}],
        },
    }
    prop_fac = texto_propuesta_factura(entidades_fac, billetera_nombre="Galicia")
    assert prop_fac == (
        "Factura de Edesur: $14.200, vence el 26/11. ¿Ya la pagaste?\n"
        "Sí: la anoto como gasto de hoy desde Galicia.\n"
        "No: la guardo en miargentum.com y ahí te aviso antes de que venza."
    )
    conv_f = _crear_conv_propuesta(db, u, "registrar_transaccion", entidades_fac)
    _, conf_fac, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=conv_f.id)
    assert conf_fac == "Listo, anoté $14.200 en Electricidad desde Galicia."

    # Comprobante sin vencimiento: propuesta y confirmación comparten subcategoría exacta
    res_doc = armar_resultado_ia_documento(
        {
            "monto": 8000,
            "tipo": "egreso",
            "moneda": "ARS",
            "categoria": "Electricidad",
            "descripcion": "Edesur",
            "fecha": hoy.isoformat(),
        },
        duplicados=[],
        billetera_nombre="Galicia",
        se_asumio_principal=False,
        billeteras_usuario=[datos["galicia"]],
        documento_tipo="factura_servicio",
        db=db,
        usuario_id=u.id,
    )
    assert res_doc["respuesta_usuario"] == (
        "¿Anoto $8.000 en Electricidad desde Galicia? Si todavía no la pagaste, decime que no."
    )
    conv_sv = _crear_conv_propuesta(db, u, "registrar_transaccion", res_doc["entidades"])
    _, conf_sv, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=conv_sv.id)
    assert conf_sv == "Listo, anoté $8.000 en Electricidad desde Galicia."

    # Factura en cuotas no pagada
    prop_2c = texto_propuesta_factura(
        {
            "factura": {
                "empresa": "EPE",
                "vencimientos": [
                    {"fecha": "2026-11-20", "monto": "1234.50"},
                    {"fecha": "2026-12-20", "monto": "1234.50"},
                ],
            }
        },
        billetera_nombre="Galicia",
    )
    assert prop_2c == (
        "Factura de EPE en 2 cuotas de $1.234,50: vencen el 20/11 y el 20/12.\n"
        "¿Ya pagaste la primera?\n"
        "Sí: la anoto como gasto de hoy desde Galicia y guardo la otra en miargentum.com.\n"
        "No: guardo las 2 en miargentum.com y ahí te aviso antes de que venzan."
    )

    conv_no = _crear_conv_propuesta(
        db,
        u,
        "registrar_transaccion",
        {
            "moneda": "ARS",
            "categoria": "Electricidad",
            "factura": {
                "empresa": "EPE",
                "origen": "whatsapp_foto",
                "vencimientos": [{"fecha": "2026-11-20", "monto": "1234.50"}],
            },
        },
    )
    assert manejar_factura_no_pagada("no", u, db, u.telefono) is True
    assert enviados[-1] == (
        "Listo, la guardé en miargentum.com. Ahí te aviso 3 días antes y el día que vence."
    )
    assert conv_no.id is not None
    assert formatear_lineas_omitidos([{"motivo": "pase_propio", "monto": 15000}]) == [
        "Salteé un pase entre tus cuentas. Mandame cada uno aparte:",
        "- $15.000",
    ]


def test_dt5_respuestas_si_no_flujos(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    def _ctx(txt: str) -> ContextoMensaje:
        return ContextoMensaje(
            datos_mensaje={},
            msg={"text": {"body": txt}},
            wamid=f"w_{uuid4().hex[:6]}",
            from_number=u.telefono,
            msg_type="text",
            t_inicio=0.0,
            usuario=u,
            db=db,
            mensaje_texto=txt,
        )

    # 1. Duplicado ("¿Lo anoto otra vez?") -> "no" y "sí"
    ent_dup = {
        "tipo_flujo": "verificacion_duplicado",
        "monto": 5000,
        "tipo": "egreso",
        "moneda": "ARS",
        "categoria": "Alimentación > Kiosco",
        "billetera_resuelta_nombre": "Galicia",
        "datos_faltantes": ["confirmar_duplicado"],
    }
    _crear_conv_propuesta(
        db, u, "verificar_duplicado", ent_dup, slot_activo=True, slot_estado=ent_dup
    )
    procesar_estados_y_handlers_deterministicos(_ctx("no"))
    assert enviados[-1] == "Listo, no lo anoto."

    _crear_conv_propuesta(
        db, u, "verificar_duplicado", ent_dup, slot_activo=True, slot_estado=ent_dup
    )
    procesar_estados_y_handlers_deterministicos(_ctx("sí"))
    assert enviados[-1] == "Listo, anoté $5.000 en Kiosco desde Galicia."

    # 2. Lote con dos iguales ("¿Anoto los dos?") -> "no" (1) y "sí" (2)
    ent_lote = {
        "tipo_flujo": "verificacion_lote_duplicado",
        "monto": 3000,
        "tipo": "egreso",
        "moneda": "ARS",
        "categoria": "Alimentación > Kiosco",
        "billetera_resuelta_nombre": "Galicia",
        "transacciones_adicionales": [
            {"monto": 3000, "tipo": "egreso", "moneda": "ARS", "categoria": "Alimentación > Kiosco"}
        ],
        "datos_faltantes": ["confirmar_lote"],
    }
    _crear_conv_propuesta(
        db, u, "verificar_lote_duplicado", ent_lote, slot_activo=True, slot_estado=ent_lote
    )
    procesar_estados_y_handlers_deterministicos(_ctx("no"))
    assert enviados[-1] == "Listo, anoté $3.000 en Kiosco desde Galicia."

    _crear_conv_propuesta(
        db, u, "verificar_lote_duplicado", ent_lote, slot_activo=True, slot_estado=ent_lote
    )
    procesar_estados_y_handlers_deterministicos(_ctx("sí"))
    assert enviados[-1] == (
        "Listo, anoté 2 gastos desde Galicia:\n"
        "- $3.000 en Kiosco\n"
        "- $3.000 en Kiosco"
    )

    # 3. Suscripción ya cobrada ("¿Lo anoto igual?") -> "no" y "sí"
    ent_sub_dup = {
        "tipo_flujo": "verificacion_duplicado_suscripcion",
        "monto": 10000,
        "tipo": "egreso",
        "moneda": "ARS",
        "categoria": "Alimentación > Kiosco",
        "billetera_resuelta_nombre": "Galicia",
        "datos_faltantes": ["confirmar_gasto_aparte"],
    }
    _crear_conv_propuesta(
        db, u, "verificar_duplicado_suscripcion", ent_sub_dup,
        slot_activo=True, slot_estado=ent_sub_dup,
    )
    procesar_estados_y_handlers_deterministicos(_ctx("no"))
    assert enviados[-1] == "Listo, no lo anoto."

    _crear_conv_propuesta(
        db, u, "verificar_duplicado_suscripcion", ent_sub_dup,
        slot_activo=True, slot_estado=ent_sub_dup,
    )
    procesar_estados_y_handlers_deterministicos(_ctx("sí"))
    assert enviados[-1] == "Listo, anoté $10.000 en Kiosco desde Galicia."

    # 4. Ambigüedad suscripción ("¿Spotify lo pagás todos los meses?") -> "no" y "sí"
    ent_amb = {"tipo_flujo": "ambiguedad_suscripcion", "servicio": "Spotify"}
    c_amb1 = _crear_conv_propuesta(
        db, u, "ambiguedad_suscripcion", ent_amb, slot_activo=True, slot_estado=ent_amb
    )
    procesar_estados_y_handlers_deterministicos(_ctx("no"))
    assert enviados[-1] == "¿Cuánto gastaste en Spotify?"
    c_amb1.slot_filling_activo = False
    db.commit()

    c_amb2 = _crear_conv_propuesta(
        db, u, "ambiguedad_suscripcion", ent_amb, slot_activo=True, slot_estado=ent_amb
    )
    procesar_estados_y_handlers_deterministicos(_ctx("sí"))
    assert enviados[-1] == (
        "¿Cada cuánto se paga Spotify? Mensual, bimestral, trimestral, semestral o anual."
    )
    c_amb2.slot_filling_activo = False
    db.commit()

    # 5. Suscripción ya existe ("¿Querés sumar otra?") -> "sí" y "no"
    ent_ex = {
        "tipo_flujo": "duplicado_suscripcion_existente",
        "servicio": "Spotify",
        "monto": 5000,
        "moneda": "ARS",
        "frecuencia": "mensual",
    }
    _crear_conv_propuesta(
        db, u, "agregar_suscripcion", ent_ex, slot_activo=True, slot_estado=ent_ex
    )
    procesar_estados_y_handlers_deterministicos(_ctx("no"))
    assert enviados[-1] == "Listo, cancelado."

    _crear_conv_propuesta(
        db, u, "agregar_suscripcion", ent_ex, slot_activo=True, slot_estado=ent_ex
    )
    procesar_estados_y_handlers_deterministicos(_ctx("sí"))
    prox = suscripcion_service.calcular_siguiente_cobro(hoy, "mensual").strftime("%d/%m")
    assert enviados[-1] == (
        f"Spotify: $5.000 por mes desde Galicia, primer cobro el {prox}.\n"
        "¿Anoto la suscripción?"
    )


def test_dt6_6_caso_sebastian_punta_a_punta(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]
    b_mp = datos["mp"]

    b_bna = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco Nación",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000"),
        saldo_actual=Decimal("100000"),
        tiene_punto_partida=True,
        fecha_saldo=date(2026, 10, 18),
        es_principal=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_mp.tiene_punto_partida = True
    b_mp.fecha_saldo = date(2026, 10, 18)
    db.add(b_bna)
    db.commit()

    # Lista 1: 3 gastos en 2 cuentas, ambas con fecha de saldo 18/10/2026
    conv_3 = _crear_conv_propuesta(
        db,
        u,
        "registrar_transaccion",
        {
            "monto": 22000,
            "tipo": "egreso",
            "moneda": "ARS",
            "categoria": "Otros",
            "billetera": "Mercado Pago",
            "billetera_origen": "Mercado Pago",
            "fecha": "2026-10-01",
            "transacciones_adicionales": [
                {
                    "monto": 7000,
                    "tipo": "egreso",
                    "moneda": "ARS",
                    "categoria": "Alimentación > Kiosco",
                    "billetera": "Banco Nación",
                    "fecha": "2026-10-04",
                },
                {
                    "monto": 17500,
                    "tipo": "egreso",
                    "moneda": "ARS",
                    "categoria": "Alimentación > Delivery",
                    "billetera": "Banco Nación",
                    "fecha": "2026-10-06",
                },
            ],
        },
    )
    _, msg_3, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=conv_3.id)
    assert msg_3 == (
        "Listo, anoté 3 gastos:\n"
        "- $22.000 en Otros · Mercado Pago · 1/10\n"
        "- $7.000 en Kiosco · Banco Nación · 4/10\n"
        "- $17.500 en Delivery · Banco Nación · 6/10\n"
        "Ojo: son de antes del 18/10: cuentan en el mes, pero no cambian tus saldos."
    )

    # Lista 2: 5 gastos desde Banco Nación (solo 1 anterior al 18/10)
    conv_5 = _crear_conv_propuesta(
        db,
        u,
        "registrar_transaccion",
        {
            "monto": 8500,
            "tipo": "egreso",
            "moneda": "ARS",
            "categoria": "Otros",
            "billetera": "Banco Nación",
            "billetera_origen": "Banco Nación",
            "fecha": "2026-10-14",
            "transacciones_adicionales": [
                {
                    "monto": 5460,
                    "tipo": "egreso",
                    "moneda": "ARS",
                    "categoria": "Alimentación > Verdulería",
                    "billetera": "Banco Nación",
                    "fecha": "2026-11-14",
                },
                {
                    "monto": 24920,
                    "tipo": "egreso",
                    "moneda": "ARS",
                    "categoria": "Otros",
                    "billetera": "Banco Nación",
                    "fecha": "2026-11-14",
                },
                {
                    "monto": 5800,
                    "tipo": "egreso",
                    "moneda": "ARS",
                    "categoria": "Otros",
                    "billetera": "Banco Nación",
                    "fecha": "2026-11-14",
                },
                {
                    "monto": 800,
                    "tipo": "egreso",
                    "moneda": "ARS",
                    "categoria": "Otros",
                    "billetera": "Banco Nación",
                    "fecha": "2026-11-14",
                },
            ],
        },
    )
    _, msg_5, _ = _confirmar_propuesta_transaccion(u, db, propuesta_id=conv_5.id)
    ultima_linea = msg_5.splitlines()[-1]
    assert ultima_linea == (
        "Ojo: el de $8.500 es de antes del 18/10: cuenta en el mes, pero no cambia tu saldo."
    )
