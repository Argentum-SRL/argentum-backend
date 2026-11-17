# -*- coding: utf-8 -*-
"""Pruebas por familia (D11 a D15 y D17 a D20)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session
import pytest

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.meta import EstadoMeta, Meta
from app.models.notificacion import (
    MENSAJE_CAMBIO_CONTRASENA_WPP,
    NivelNotificacion,
    Notificacion,
    TipoNotificacion,
)
from app.models.transaccion import (
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.routers.whatsapp import aviso_personas_wpp
from app.routers.whatsapp.aviso_frescura_wpp import linea_frescura_wpp
from app.routers.whatsapp.aviso_saldo_wpp import obtener_aviso_saldo
from app.routers.whatsapp.confirmaciones import (
    _confirmar_propuesta_baja_suscripcion,
    _confirmar_propuesta_suscripcion,
)
from app.routers.whatsapp.constantes import texto_cupo_wpp
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.routers.whatsapp.etapa_entrada import (
    procesar_entrada_medios_y_texto,
    procesar_vinculacion_o_desconocido,
)
from app.routers.whatsapp.gastos import _formatear_respuesta_gastos
from app.routers.whatsapp.handlers_confirmaciones import (
    manejar_confirmacion,
    manejar_saludo,
)
from app.routers.whatsapp.handlers_menus import manejar_numero_aislado
from app.routers.whatsapp.handlers_suscripciones import (
    manejar_alta_suscripcion,
    manejar_ambiguedad_suscripcion,
    manejar_baja_suscripcion,
    manejar_consulta_suscripciones,
)
from app.routers.whatsapp.metas import (
    _confirmar_propuesta_aporte_meta,
    manejar_aporte_meta,
)
from app.routers.whatsapp.parsers import (
    _interpretar_cuotas,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.routers.whatsapp.que_es_plata_wpp import manejar_respuesta_numerada
from app.routers.whatsapp.recordatorio_impago_wpp import agregar_recordatorio_impago
from app.routers.whatsapp.registro import _registrar_directo_si_corresponde
from app.routers.whatsapp.reintegro_wpp import registrar_reintegro_wpp
from app.routers.whatsapp.resolvers_cascada import _validar_item_movimiento
from app.routers.whatsapp.transferencias import _confirmar_propuesta_transferencia
from app.routers.whatsapp.transferencias_flujo import (
    _ejecutar_tabla_flujo,
    _proponer_transferencia_ia,
    _registrar_transferencia_directa,
)
from app.services import (
    fin_de_mes_textos,
    memoria_comercio_service,
    permitirse_textos,
    suscripcion_service,
    whatsapp_service,
)
from app.services.frescura_service import Frescura
from app.services.whatsapp_service import buzon_de_respuesta, enviar_whatsapp
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


def test_d11_transferencias_y_dolares(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]
    b_g = datos["galicia"]
    b_mp = datos["mp"]
    b_usd = datos["usd"]

    assert _proponer_transferencia_ia(
        b_g, b_mp, {"monto": 20000, "fecha": hoy}, "pasé 20 mil", u, db, u.telefono, None
    )
    assert enviados[-1] == "¿Paso $20.000 de Galicia a Mercado Pago?"

    assert _registrar_transferencia_directa(
        b_g,
        b_mp,
        {"monto": Decimal("20000"), "fecha": date(2026, 11, 15), "comision": Decimal("500")},
        "pasé 20 mil ayer con 500 de comision",
        u,
        db,
        u.telefono,
        None,
    )
    assert enviados[-1] == (
        "Listo. Pasaste $20.000 de Galicia a Mercado Pago (ayer), con $500 de comisión."
    )

    _ejecutar_tabla_flujo(
        {"tipo": "extraccion", "moneda": "ARS", "monto": Decimal("5000")},
        "saqué 5000",
        u,
        db,
        u.telefono,
        None,
        [b_g, b_mp, b_usd],
    )
    assert enviados[-1] == "No tenés una cuenta de efectivo en pesos. Creala en miargentum.com"

    _ejecutar_tabla_flujo(
        {"billetera_origen_obj": b_g, "billetera_destino_obj": b_g, "monto": Decimal("1000")},
        "pasé de galicia a galicia",
        u,
        db,
        u.telefono,
        None,
        [b_g, b_mp],
    )
    assert enviados[-1] == (
        "Pusiste la misma cuenta de los dos lados. ¿A qué otra cuenta lo pasaste?"
    )

    conv_c = _crear_conv_propuesta(
        db,
        u,
        "transferir_fondos",
        {
            "tipo_operacion": "compra_usd",
            "billetera_origen_id": str(b_g.id),
            "billetera_destino_id": str(b_usd.id),
            "monto": 100000,
            "monto_origen": 100000,
            "monto_destino": 100,
            "moneda_origen": "ARS",
            "moneda_destino": "USD",
            "fecha": hoy.isoformat(),
        },
    )
    _, msg_comp, _ = _confirmar_propuesta_transferencia(u, db, propuesta_id=conv_c.id)
    assert msg_comp == "Listo. Compraste US$100 por $100.000."


def test_d12_errores_y_limites(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    assert texto_cupo_wpp("tope dia", "mensaje") == (
        "Por hoy llegué al máximo de mensajes. Mañana seguimos; mientras, cargá en miargentum.com"
    )
    assert texto_cupo_wpp("tope fotos", "archivo") == (
        "Por hoy no leo más fotos ni archivos. Escribime el gasto, por ejemplo: _2500 súper_."
    )
    assert texto_cupo_wpp("este mes", "mensaje") == (
        "Este mes ya no puedo responder por acá. Cargá tus movimientos en miargentum.com"
    )

    assert manejar_numero_aislado("5000", u, db, u.telefono) is True
    assert enviados[-1] == "Mandaste solo un número. Decime en qué, por ejemplo: _5000 kiosco_."

    _, av_fut = _resolver_y_validar_fecha(hoy + timedelta(days=2))
    assert av_fut == "Todavía no pasó esa fecha: va con fecha de hoy."
    _, av_60 = _resolver_y_validar_fecha(hoy - timedelta(days=70))
    assert av_60 == "Solo anoto hasta 60 días para atrás: va con fecha de hoy."
    _, _, _, _, err_c = _interpretar_cuotas("en 60 cuotas", Decimal("1000"))
    assert err_c == "Las cuotas tienen que ser entre 1 y 48."

    _, mot = _validar_item_movimiento(
        {"monto": 100, "moneda": "USD", "descripcion": "Netflix"},
        "Galicia",
        Moneda.ARS,
        [datos["galicia"]],
    )
    assert mot == "No pude anotar Netflix (US$100): es en dólares y Galicia es en pesos."

    ctx_largo = ContextoMensaje(
        datos_mensaje={},
        msg={"text": {"body": "a" * 1501}},
        wamid="w_largo",
        from_number=u.telefono,
        msg_type="text",
        t_inicio=0.0,
        usuario=u,
        db=db,
    )
    procesar_entrada_medios_y_texto(ctx_largo)
    assert enviados[-1] == "El mensaje es muy largo (máximo 1.500 caracteres). Mandámelo más corto."


def test_d13_consultas(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    monkeypatch.setattr(
        "app.services.contexto_financiero_service._calcular_saldo_disponible_sync",
        lambda db_, uid_: {
            "ars": {"total_billeteras": 150000, "saldo_disponible": 120000},
            "usd": {"total_billeteras": 1000, "saldo_disponible": 900},
            "fecha_fin_ciclo": date(2026, 11, 30),
        },
    )
    monkeypatch.setattr(
        "app.services.frescura_service.calcular_frescura",
        lambda db_, uid_: Frescura(
            datos_viejos=False,
            dias_sin_cargar=1,
            umbral_dias=7,
            ultima_carga=hoy,
            intervalos=5,
        ),
    )
    res_ia = {"intent": "consultar_saldo", "entidades": {}}
    enriquecer_respuesta_por_intent("consultar_saldo", res_ia, "cuánta plata tengo", u, db)
    assert res_ia["respuesta_usuario"] == (
        "Tenés $150.000 en tus cuentas.\n"
        "Libres: $120.000, ya descontado lo que vence hasta el 30/11.\n"
        "En dólares: US$1.000 (libres US$900)."
    )

    txt_gastos = _formatear_respuesta_gastos(
        "Este mes",
        None,
        False,
        45000,
        3,
        0,
        0,
        [("Supermercado", 30000), ("Kiosco", 15000)],
        desde=date(2026, 11, 1),
    )
    assert txt_gastos == (
        "Este mes (desde el 01/11) gastaste $45.000 en 3 gastos.\n"
        "Lo que más pesó: Supermercado ($30.000) y Kiosco ($15.000)."
    )

    assert manejar_consulta_suscripciones("mis suscripciones", u, db, u.telefono) is True
    assert enviados[-1] == "No tenés suscripciones activas."


def test_d14_permitirse_y_fin_de_mes():
    r_wpp = permitirse_textos.textos_permitirse(
        {
            "modo": "cuotas",
            "resultado": "justo",
            "usa_cuentas": True,
            "uso_mensual": Decimal("25000"),
            "cantidad_cuotas": 1,
            "plan": SimpleNamespace(
                monto_cuota=Decimal("50000"),
                total=Decimal("50000"),
                interes=Decimal("0"),
                valor_presente=None,
            ),
        }
    )
    msg_p = permitirse_textos.armar_respuesta_permitirse(r_wpp, {"nota_interes": True})
    assert msg_p == (
        "Te entra justo.\n"
        "En un pago son $50.000.\n"
        "De cada cuota, unos $25.000 salen de la plata que ya tenés en tus cuentas.\n"
        "Te alcanza para pagarlo.\n"
        "Si no es urgente, esperá 24 horas y volvé a fijarte.\n"
        "Lo calculé sin interés. Si tiene, decime cuánto por año (por ejemplo, 60%)."
    )

    r_no = permitirse_textos.textos_permitirse(
        {
            "modo": "mensual",
            "resultado": "no_alcanza",
            "ahorros_total": Decimal("0"),
            "meses_cuentas": 3,
        }
    )
    assert permitirse_textos.armar_respuesta_permitirse(r_no, {}) == (
        "No te alcanza: con la plata de tus cuentas lo pagás solo 3 meses."
    )

    r_fdm = SimpleNamespace(
        estado="datos_viejos",
        libre_ars=Decimal("20000"),
        libre_usd=Decimal("50"),
        frescura=Frescura(
            datos_viejos=True,
            dias_sin_cargar=15,
            umbral_dias=7,
            ultima_carga=date(2026, 11, 1),
            intervalos=6,
        ),
    )
    assert fin_de_mes_textos.mensaje_wpp(r_fdm) == (
        "Todavía no tengo suficientes meses tuyos para decirte con cuánto llegás.\n"
        "Hoy tenés $20.000 libres.\n"
        "En dólares: US$50 libres.\n"
        "Ojo: hace 15 días que no cargás nada, puede no estar al día."
    )
    assert fin_de_mes_textos.linea_cobertura(Decimal("1.5")) == (
        "Como puede faltar algo por cargar, te doy un número más bajo y uno más alto."
    )


def test_d15_avisos_de_una_linea(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]
    b_g = datos["galicia"]
    b_mp = datos["mp"]
    b_g.tiene_punto_partida = True
    b_g.fecha_saldo = date(2026, 10, 18)
    b_mp.tiene_punto_partida = True
    b_mp.fecha_saldo = date(2026, 10, 18)
    db.commit()

    # 1 movimiento anterior
    assert obtener_aviso_saldo(
        [{
            "billetera": b_g,
            "fecha": date(2026, 10, 10),
            "metodo_pago": MetodoPago.DEBITO,
            "monto": 1000,
        }]
    ) == "Ojo: es de antes del 18/10: cuenta en el mes, pero no cambia tu saldo."

    # Varios de una cuenta y todos anteriores
    assert obtener_aviso_saldo(
        [
            {"billetera": b_g, "fecha": date(2026, 10, 10), "monto": 1000,
             "metodo_pago": MetodoPago.DEBITO},
            {"billetera": b_g, "fecha": date(2026, 10, 11), "monto": 2000,
             "metodo_pago": MetodoPago.DEBITO},
        ]
    ) == "Ojo: son de antes del 18/10: cuentan en el mes, pero no cambian tu saldo."

    # Algunos de una lista con uno solo anterior
    assert obtener_aviso_saldo(
        [
            {"billetera": b_g, "fecha": date(2026, 10, 14), "monto": 8500,
             "metodo_pago": MetodoPago.DEBITO},
            {"billetera": b_g, "fecha": date(2026, 11, 14), "monto": 5000,
             "metodo_pago": MetodoPago.DEBITO},
        ]
    ) == "Ojo: el de $8.500 es de antes del 18/10: cuenta en el mes, pero no cambia tu saldo."

    # Algunos de una lista con 2 anteriores de la misma cuenta
    assert obtener_aviso_saldo(
        [
            {"billetera": b_g, "fecha": date(2026, 10, 14), "monto": 8500,
             "metodo_pago": MetodoPago.DEBITO},
            {"billetera": b_g, "fecha": date(2026, 10, 15), "monto": 3500,
             "metodo_pago": MetodoPago.DEBITO},
            {"billetera": b_g, "fecha": date(2026, 11, 14), "monto": 5000,
             "metodo_pago": MetodoPago.DEBITO},
        ]
    ) == "Ojo: 2 son de antes del 18/10: cuentan en el mes, pero no cambian tu saldo."

    # Varias cuentas, misma fecha de saldo y todos anteriores
    assert obtener_aviso_saldo(
        [
            {"billetera": b_mp, "fecha": date(2026, 10, 1), "monto": 22000,
             "metodo_pago": MetodoPago.DEBITO},
            {"billetera": b_g, "fecha": date(2026, 10, 4), "monto": 7000,
             "metodo_pago": MetodoPago.DEBITO},
        ]
    ) == "Ojo: son de antes del 18/10: cuentan en el mes, pero no cambian tus saldos."

    # Varias cuentas en otro caso (fechas distintas)
    b_mp.fecha_saldo = date(2026, 10, 20)
    assert obtener_aviso_saldo(
        [
            {"billetera": b_mp, "fecha": date(2026, 10, 1), "monto": 22000,
             "metodo_pago": MetodoPago.DEBITO},
            {"billetera": b_g, "fecha": date(2026, 10, 4), "monto": 7000,
             "metodo_pago": MetodoPago.DEBITO},
        ]
    ) == "Ojo: 2 son de antes de que cargaras tus saldos: cuentan en el mes, pero no los cambian."

    # Frescura y personas
    assert linea_frescura_wpp(
        Frescura(True, 10, 7, date(2026, 11, 6), 5)
    ) == "Ojo: hace 10 días que no cargás nada, puede no estar al día."
    assert aviso_personas_wpp.TEXTO_AVISO_PAGOS == (
        "Si te acordás para qué era, decime _era el súper_ y lo cambio."
    )

    # Recordatorio impago
    notif = Notificacion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoNotificacion.RESUMEN_IMPAGO,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje="impago",
        canal_whatsapp=True,
        enviada_whatsapp=False,
        archivada=False,
        entidad_tipo="tarjeta",
        entidad_id=datos["tarjeta"].id,
        datos_template={"vencimiento": "2026-11-04"},
    )
    db.add(notif)
    db.commit()
    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *a, **kw: {"monto_deuda_restante": Decimal("50000")},
    )
    mock_env = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_env)
    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Listo.")
        assert agregar_recordatorio_impago(db, u) is True
    assert mock_env.call_args[1]["texto"] == (
        "Listo.\nOjo: el resumen de Visa Galicia •••• 1506 de "
        "$50.000 venció el 04/11 y sigue sin pagar."
    )


def test_d17_reintegros_y_que_es_esta_plata(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    compra = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=datos["galicia"].id,
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_super"].id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 15),
        metodo_pago=MetodoPago.DEBITO,
        descripcion="Súper",
        origen=OrigenTransaccion.IA_WPP,
    )
    cobro = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=datos["galicia"].id,
        categoria_id=datos["cat_ing"].id,
        subcategoria_id=datos["sub_sueldo"].id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("10000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        descripcion="Juan",
        origen=OrigenTransaccion.IA_WPP,
    )
    db.add_all([compra, cobro])
    db.commit()

    txt_r = registrar_reintegro_wpp(
        db, u, compra, Decimal("25000"), "me devolvieron 25000 del super"
    )
    assert txt_r == (
        "Listo, anoté el reintegro de $25.000 de Súper (ayer).\n"
        "Si no era esa compra, decime _borrá eso_."
    )

    _crear_conv_propuesta(db, u, "que_es_esta_plata", {"transaccion_id": str(cobro.id)})
    assert manejar_respuesta_numerada("1", u, db, u.telefono) is True
    assert enviados[-1] == "Listo, queda como cobro."


def test_d18_suscripciones(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    assert manejar_ambiguedad_suscripcion("pagué spotify", u, db, u.telefono) is True
    assert enviados[-1] == "¿Spotify lo pagás todos los meses?"

    conv_act = db.execute(select(ConversacionWpp)).scalars().first()
    conv_act.slot_filling_activo = False
    db.commit()

    assert manejar_alta_suscripcion(
        "empecé a pagar Netflix 10000 por mes", u, db, u.telefono
    ) is True
    prox = suscripcion_service.calcular_siguiente_cobro(hoy, "mensual").strftime("%d/%m")
    assert enviados[-1] == (
        f"Netflix: $10.000 por mes desde Galicia, primer cobro el {prox}.\n"
        "¿Anoto la suscripción?"
    )

    _, msg_conf, _ = _confirmar_propuesta_suscripcion(u, db)
    assert msg_conf == (
        f"Listo, anoté Netflix: $10.000 por mes desde Galicia.\nPrimer cobro: {prox}."
    )

    assert manejar_baja_suscripcion("di de baja Netflix", u, db, u.telefono) is True
    assert enviados[-1] == "¿Doy de baja Netflix ($10.000 por mes)?"
    _, msg_baja, _ = _confirmar_propuesta_baja_suscripcion(u, db)
    assert msg_baja == "Listo, di de baja Netflix."


def test_d19_metas(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    meta = Meta(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Vacaciones",
        monto_objetivo=Decimal("100000"),
        monto_actual=Decimal("0"),
        moneda=Moneda.ARS,
        estado=EstadoMeta.ACTIVA,
    )
    db.add(meta)
    db.commit()

    assert manejar_aporte_meta("aporté 20000 a la meta Vacaciones", u, db, u.telefono) is True
    assert enviados[-1] == "¿Aporto $20.000 a tu meta Vacaciones desde Galicia?"

    _, msg_ap, _ = _confirmar_propuesta_aporte_meta(u, db)
    assert msg_ap == "Listo, aporté $20.000 a tu meta Vacaciones."


def test_d20_saludo_bienvenida_y_cuenta(db: Session, enviados, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]

    assert manejar_saludo("hola", u, db, u.telefono) is True
    assert enviados[-1] == (
        "¡Hola! Mandame un gasto, como _gasté 5000 en el kiosco_, o preguntá _cuánta plata tengo_."
    )

    _crear_conv_propuesta(
        db,
        u,
        "slot_filling",
        {},
        slot_activo=True,
        slot_estado={"monto": 4000, "categoria": "Alimentación > Kiosco", "moneda": "ARS"},
    )
    assert manejar_saludo("hola", u, db, u.telefono) is True
    assert enviados[-1] == (
        "¡Hola! Quedó a medias anotar $4.000 en Kiosco.\n"
        "Contestá la pregunta de arriba o mandame otro gasto."
    )

    ctx_no_reg = ContextoMensaje(
        datos_mensaje={},
        msg={"text": {"body": "hola"}},
        wamid="w_nr",
        from_number="+5491199998888",
        msg_type="text",
        t_inicio=0.0,
        usuario=None,
        db=db,
    )
    monkeypatch.setattr(
        "app.routers.whatsapp.etapa_entrada._debe_responder_no_registrado",
        lambda *a: True,
    )
    procesar_vinculacion_o_desconocido(ctx_no_reg)
    assert enviados[-1] == "No encontré tu cuenta. Creala en miargentum.com"

    assert MENSAJE_CAMBIO_CONTRASENA_WPP == (
        "Se cambió la contraseña de tu cuenta de Argentum.\n"
        "Si no fuiste vos, cambiala ya: https://miargentum.com/auth/recuperar-password"
    )


def test_c2_fecha_futura_propuesta_y_directo(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]
    fecha_fut = (hoy + timedelta(days=5)).isoformat()
    entidades = {
        "monto": 5000,
        "tipo": "egreso",
        "moneda": "ARS",
        "categoria": "Alimentación > Kiosco",
        "billetera_origen": "Galicia",
        "descripcion": "kiosco",
        "fecha": fecha_fut,
    }
    prop = _construir_propuesta_transaccion(
        entidades, "Galicia", db=db, usuario_id=u.id
    )
    assert prop == (
        "Todavía no pasó esa fecha: va con fecha de hoy.\n"
        "¿Anoto $5.000 en Kiosco desde Galicia?"
    )

    conv_dir = ConversacionWpp(
        usuario_id=u.id,
        wamid=f"wamid_{uuid4().hex[:8]}",
        mensaje_usuario="gasté 5000 en el kiosco el 21/11",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot=prop,
        intent_detectado="registrar_transaccion",
        entidades=entidades,
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        slot_filling_activo=False,
        slot_filling_estado=None,
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv_dir)
    db.flush()
    reg_ok, msg_dir = _registrar_directo_si_corresponde(u, db, conv_dir)
    assert reg_ok is True
    assert msg_dir == (
        "Listo, anoté $5.000 en Kiosco desde Galicia.\n"
        "Todavía no pasó esa fecha: va con fecha de hoy."
    )


def test_c3_corregir_categoria_con_buzon(db: Session, monkeypatch):
    hoy = date(2026, 11, 16)
    _fijar_hoy(monkeypatch, hoy)
    datos = _crear_escenario_base(db, hoy)
    u = datos["usuario"]
    b_gal = datos["galicia"]
    mock_env = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_env)

    def _crear_tx_y_corr(cambios: dict) -> str:
        tx = Transaccion(
            id=uuid4(),
            usuario_id=u.id,
            billetera_id=b_gal.id,
            categoria_id=datos["cat_alim"].id,
            subcategoria_id=datos["sub_kiosco"].id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000"),
            moneda=Moneda.ARS,
            fecha=hoy,
            metodo_pago=MetodoPago.DEBITO,
            descripcion="kiosco",
            origen=OrigenTransaccion.IA_WPP,
        )
        db.add(tx)
        db.commit()
        _crear_conv_propuesta(
            db, u, "corregir", {"transaccion_id": str(tx.id), "cambios": cambios}
        )
        mock_env.reset_mock()
        with buzon_de_respuesta(u.telefono):
            assert manejar_confirmacion("sí", u, db, u.telefono) is True
        return mock_env.call_args[1]["texto"]

    # 1. Solo categoría con pregunta de memoria
    c_solo = {
        "categoria_id": str(datos["cat_alim"].id),
        "subcategoria_id": str(datos["sub_super"].id),
        "categoria_nombre": "Supermercado",
    }
    assert _crear_tx_y_corr(c_solo) == (
        "Listo, lo pasé a Supermercado.\n"
        "¿Siempre que digas _kiosco_ lo pongo ahí?"
    )

    # 2. Categoría + monto con pregunta de memoria (limpia la memoria previa si existiera)
    c_varios = {
        "monto": 3000,
        "categoria_id": str(datos["cat_alim"].id),
        "subcategoria_id": str(datos["sub_super"].id),
        "categoria_nombre": "Supermercado",
    }
    assert _crear_tx_y_corr(c_varios) == (
        "Listo, quedó $3.000 en Supermercado desde Galicia.\n"
        "¿Siempre que digas _kiosco_ lo pongo ahí?"
    )

    # 3. Sin pregunta de memoria (ya guardada en Supermercado)
    memoria_comercio_service.guardar(
        db=db,
        usuario_id=u.id,
        descripcion="kiosco",
        tipo="egreso",
        categoria_id=datos["cat_alim"].id,
        subcategoria_id=datos["sub_super"].id,
        commit=True,
    )
    assert _crear_tx_y_corr(c_solo) == (
        "Listo, quedó $5.000 en Supermercado desde Galicia."
    )

