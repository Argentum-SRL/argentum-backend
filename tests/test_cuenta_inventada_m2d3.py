# -*- coding: utf-8 -*-
"""
Pruebas de rechazo de cuenta o tarjeta inventada por la IA (D-C1 y D-C2, TC1 a TC11).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import MetodoPago, Transaccion
from app.models.usuario import (
    AuthProvider,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.routers.whatsapp import extraccion_documento
from app.routers.whatsapp_ia import _procesar_mensaje_whatsapp_background
from app.services import ai_service, openai_client
from app.utils.fecha import TZ_ARGENTINA
from app.utils.telefono import normalizar_telefono_ar
from tests.wpp_familias_helpers import (
    aplicar_compat_sqlite,
    crear_db_sqlite,
    instalar_enviados,
)


@pytest.fixture(autouse=True)
def _entorno_seguro(monkeypatch):
    """Compatibilidad SQLite y bloqueo total de clientes reales OpenAI/Meta."""
    aplicar_compat_sqlite(monkeypatch)

    def _prohibido(*_a, **_kw):
        raise AssertionError("Llamada real a OpenAI o Meta prohibida en tests")

    monkeypatch.setattr(openai_client, "get_openai_client", _prohibido)
    monkeypatch.setattr(ai_service, "_get_client", _prohibido)
    monkeypatch.setattr(
        "app.routers.whatsapp_ia.marcar_leido_y_escribiendo",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "app.routers.whatsapp_ia._verificar_rate_limit_registrado",
        lambda *_a, **_kw: (True, None),
    )
    fijo = datetime(2026, 11, 19, 14, 0, tzinfo=TZ_ARGENTINA)
    monkeypatch.setattr("app.utils.fecha.ahora_argentina", lambda: fijo)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fijo.date())
    for mod in (
        "app.routers.whatsapp.parsers",
        "app.routers.whatsapp.propuestas",
        "app.routers.whatsapp.registro",
        "app.routers.whatsapp.deshacer_corregir",
        "app.routers.whatsapp.handlers_confirmaciones",
        "app.routers.whatsapp.handlers_deshacer",
        "app.routers.whatsapp.handlers_menus",
        "app.routers.whatsapp.factura_wpp",
        "app.routers.whatsapp.lote_documento",
        "app.routers.whatsapp_ia",
    ):
        try:
            monkeypatch.setattr(f"{mod}.hoy_argentina", lambda d=fijo.date(): d)
        except AttributeError:
            pass


@pytest.fixture(name="db", scope="function")
def db_fixture(monkeypatch):
    """Sesión SQLite en memoria por test."""
    session = crear_db_sqlite(monkeypatch)
    for mod_path in (
        "app.core.database.SessionLocal",
        "app.routers.whatsapp_ia.SessionLocal",
        "app.services.salientes_wpp_service.SessionLocal",
        "app.services.rate_limit_service.SessionLocal",
        "app.services.uso_ia_service.SessionLocal",
    ):
        try:
            monkeypatch.setattr(mod_path, lambda: session)
        except AttributeError:
            pass
    old_close = session.close
    monkeypatch.setattr(session, "close", lambda: None)
    try:
        yield session
    finally:
        old_close()


def _seed_catalogo(db: Session) -> None:
    orden = 1
    for cat_def in ARBOL_CATEGORIAS:
        t_cat = (
            TipoCategoria.INGRESO
            if str(cat_def.tipo) == "ingreso"
            else TipoCategoria.EGRESO
        )
        cat = Categoria(
            id=uuid4(),
            nombre=cat_def.nombre,
            tipo=t_cat,
            orden=orden,
            estado=EstadoCategoria.ACTIVA,
        )
        db.add(cat)
        db.flush()
        orden += 1
        for s_ord, sub_def in enumerate(cat_def.subcategorias, start=1):
            sub = Subcategoria(
                id=uuid4(),
                categoria_id=cat.id,
                nombre=sub_def.nombre,
                orden=s_ord,
                es_sistema=sub_def.es_sistema,
                estado=EstadoSubcategoria.ACTIVA,
            )
            db.add(sub)
    db.flush()


def _crear_usuario_m2d3(db: Session, galicia_principal: bool = True) -> dict:
    _seed_catalogo(db)
    tel = "+5491155550093"
    u = Usuario(
        id=uuid4(),
        nombre="Tester",
        apellido="M2d3",
        email="m2d3@argentum.test",
        telefono=tel,
        telefono_normalizado=normalizar_telefono_ar(tel),
        telefono_verificado=True,
        moneda_principal=Moneda.ARS,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
    )
    db.add(u)
    db.flush()

    b_gal = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000"),
        saldo_actual=Decimal("1000000"),
        fecha_saldo=date(2026, 11, 1),
        es_principal=galicia_principal,
        es_efectivo=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_sant = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Santander",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000"),
        saldo_actual=Decimal("500000"),
        fecha_saldo=date(2026, 11, 1),
        es_principal=False,
        es_efectivo=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_ef_ars = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Efectivo Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("200000"),
        saldo_actual=Decimal("200000"),
        fecha_saldo=date(2026, 11, 1),
        es_principal=False,
        es_efectivo=True,
        estado=EstadoBilletera.ACTIVA,
    )
    b_ef_usd = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Efectivo Dólares",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("1000"),
        saldo_actual=Decimal("1000"),
        fecha_saldo=date(2026, 11, 1),
        es_principal=False,
        es_efectivo=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_gal, b_sant, b_ef_ars, b_ef_usd])
    db.flush()

    t1 = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_gal.id,
        nombre="•••• 1506",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=24,
        dia_vencimiento=4,
        estado=EstadoTarjeta.ACTIVA,
    )
    t2 = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_gal.id,
        nombre="•••• 2745",
        red=RedTarjeta.AMEX,
        moneda=Moneda.ARS,
        dia_cierre=24,
        dia_vencimiento=4,
        estado=EstadoTarjeta.ACTIVA,
    )
    t3 = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_sant.id,
        nombre="•••• 5077",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=20,
        dia_vencimiento=5,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add_all([t1, t2, t3])
    db.commit()
    return {
        "usuario": u,
        "galicia": b_gal,
        "santander": b_sant,
        "efectivo_ars": b_ef_ars,
        "efectivo_usd": b_ef_usd,
        "visa_galicia": t1,
        "amex_galicia": t2,
        "visa_santander": t3,
    }


def _enviar(tel: str, texto: str) -> None:
    _procesar_mensaje_whatsapp_background(
        {
            "msg": {
                "id": f"wamid.{uuid4().hex}",
                "from": tel,
                "type": "text",
                "text": {"body": texto},
            },
            "wamid": f"wamid.{uuid4().hex}",
            "from_number": tel,
            "msg_type": "text",
            "t_inicio": 0.0,
        }
    )


def _resp_grabacion(
    monto: float,
    categoria: str,
    descripcion: str,
    billetera: str | None = None,
    billetera_origen: str | None = None,
    tarjeta: str | None = None,
    persona: str | None = None,
    adicionales: list[dict] | None = None,
    confianza: float = 0.9,
) -> dict:
    return {
        "intent": "registrar_transaccion",
        "confianza": confianza,
        "slot_filling": False,
        "entidades": {
            "monto": monto,
            "moneda": "ARS",
            "tipo": "egreso",
            "categoria": categoria,
            "descripcion": descripcion,
            "servicio": None,
            "frecuencia": None,
            "billetera": billetera,
            "billetera_origen": billetera_origen,
            "billetera_destino": None,
            "cantidad_cuotas": None,
            "fecha": None,
            "destinatario": persona,
            "persona": persona,
            "tasa_cambio": None,
            "monto_destino": None,
            "monto_comision": None,
            "confirmado": None,
            "tarjeta": tarjeta,
            "meta": None,
            "transacciones_adicionales": adicionales or [],
        },
        "datos_faltantes": [],
        "respuesta_usuario": "Listo.",
    }


def test_tc1_transferencia_a_juan_descarta_santander(db, monkeypatch):
    """TC1: 'le transferí 6000 a juan' con IA devolviendo Santander -> Galicia."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    resp = _resp_grabacion(
        6000.0,
        "Pagos a personas",
        "Transferencia a juan",
        billetera="Santander",
        billetera_origen="Santander",
        persona="Juan",
    )
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: resp)

    _enviar(u.telefono, "le transferí 6000 a juan")

    assert enviados[-1] == "Listo, anoté $6.000 en Pagos a personas desde Galicia."
    tx = db.execute(select(Transaccion)).scalar_one()
    assert tx.billetera_id == esc["galicia"].id
    assert tx.monto == Decimal("6000")


def test_tc2_tarjeta_debito_descarta_santander(db, monkeypatch):
    """TC2: 'gasté 5000 con la tarjeta de débito en el kiosco' con IA Santander -> Galicia."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    resp = _resp_grabacion(
        5000.0,
        "Kiosco",
        "Kiosco",
        billetera_origen="Santander",
    )
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: resp)

    _enviar(u.telefono, "gasté 5000 con la tarjeta de débito en el kiosco")

    assert enviados[-1] == "Listo, anoté $5.000 en Kiosco desde Galicia."
    tx = db.execute(select(Transaccion)).scalar_one()
    assert tx.billetera_id == esc["galicia"].id
    assert tx.tarjeta_id is None


def test_tc3_pago_a_caro_descarta_santander(db, monkeypatch):
    """TC3: 'le mandé 12 mil a caro por la cena' con IA Santander -> Galicia."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    resp = _resp_grabacion(
        12000.0,
        "Restaurantes",
        "Cena",
        billetera_origen="Santander",
        persona="Caro",
    )
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: resp)

    _enviar(u.telefono, "le mandé 12 mil a caro por la cena")

    assert enviados[-1] == "Listo, anoté $12.000 en Restaurantes desde Galicia."
    tx = db.execute(select(Transaccion)).scalar_one()
    assert tx.billetera_id == esc["galicia"].id


def test_tc4_nombra_santander_la_usa(db, monkeypatch):
    """TC4: 'gasté 5000 en el kiosco desde santander' -> Santander."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    resp = _resp_grabacion(
        5000.0,
        "Kiosco",
        "Kiosco",
        billetera_origen="Santander",
    )
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: resp)

    _enviar(u.telefono, "gasté 5000 en el kiosco desde santander")

    assert enviados[-1] == "Listo, anoté $5.000 en Kiosco desde Santander."
    tx = db.execute(select(Transaccion)).scalar_one()
    assert tx.billetera_id == esc["santander"].id


def test_tc5_alias_rio_usa_santander(db, monkeypatch):
    """TC5: 'pagué 7000 con el rio en la farmacia' con IA Santander -> Santander (alias)."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    resp = _resp_grabacion(
        7000.0,
        "Salud > Farmacia",
        "Farmacia",
        billetera="Santander",
        billetera_origen="Santander",
    )
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: resp)

    _enviar(u.telefono, "pagué 7000 con el rio en la farmacia")

    assert enviados[-1] == "Listo, anoté $7.000 en Farmacia desde Santander."
    tx = db.execute(select(Transaccion)).scalar_one()
    assert tx.billetera_id == esc["santander"].id


def test_tc6_efectivo_manda_sobre_galicia_ia(db, monkeypatch):
    """TC6: 'taxi 6500 en efectivo' con IA Galicia -> Efectivo Pesos."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    resp = _resp_grabacion(
        6500.0,
        "Transporte > Taxi y apps",
        "Taxi",
        billetera="Galicia",
        billetera_origen="Galicia",
    )
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: resp)

    _enviar(u.telefono, "taxi 6500 en efectivo")

    assert enviados[-1] == "Listo, anoté $6.500 en Taxi y apps desde Efectivo Pesos."
    tx = db.execute(select(Transaccion)).scalar_one()
    assert tx.billetera_id == esc["efectivo_ars"].id


def test_tc7_menu_sin_principal_y_respuesta_slot(db, monkeypatch):
    """TC7: sin principal abre menú y respuesta directa o vía IA resuelve Santander."""
    esc = _crear_usuario_m2d3(db, galicia_principal=False)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = _resp_grabacion(5000.0, "Kiosco", "Kiosco", billetera_origen="Santander")
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    _enviar(u.telefono, "gasté 5000 en el kiosco")
    assert "¿De qué cuenta salió?" in enviados[-1]
    assert "Santander" in enviados[-1]

    # Respuesta directa al menú por nombre "santander" (como hoy)
    _enviar(u.telefono, "santander")
    assert enviados[-1] == "Listo, anoté $5.000 en Kiosco desde Santander."
    tx1 = db.execute(select(Transaccion)).scalar_one()
    assert tx1.billetera_id == esc["santander"].id

    # Respuesta numérica "3" (Santander es la 3 en orden alfabético)
    actual = _resp_grabacion(5200.0, "Kiosco", "Kiosco", billetera_origen="Santander")
    _enviar(u.telefono, "gasté 5200 en el kiosco")
    assert "¿De qué cuenta salió?" in enviados[-1]
    _enviar(u.telefono, "3")
    assert enviados[-1] == "Listo, anoté $5.200 en Kiosco desde Santander."

    # Respuesta que cae a IA mientras el menú pide billetera (ej: "la tercera") -> D-C1(b)
    actual = _resp_grabacion(5500.0, "Kiosco", "Kiosco", billetera_origen="Santander")
    _enviar(u.telefono, "gasté 5500 en el kiosco")
    assert "¿De qué cuenta salió?" in enviados[-1]
    actual = _resp_grabacion(5500.0, "Kiosco", "Kiosco", billetera_origen="Santander")
    _enviar(u.telefono, "la tercera")
    assert enviados[-1] == "¿Anoto $5.500 en Kiosco desde Santander?"
    actual = {
        "intent": "confirmar",
        "confianza": 0.95,
        "slot_filling": False,
        "entidades": {},
    }
    _enviar(u.telefono, "sí")
    assert enviados[-1] == "Listo, anoté $5.500 en Kiosco desde Santander."
    txs = db.execute(select(Transaccion).order_by(Transaccion.monto.asc())).scalars().all()
    assert len(txs) == 3
    assert all(t.billetera_id == esc["santander"].id for t in txs)


def test_tc8_correccion_cuenta_como_hoy(db, monkeypatch):
    """TC8: 'almuerzo 12 lucas' anota Galicia y 'eso fue con santander' propone cambio."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = _resp_grabacion(12000.0, "Gastronomía", "Almuerzo")
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    _enviar(u.telefono, "almuerzo 12 lucas")
    assert enviados[-1] == "Listo, anoté $12.000 en Gastronomía desde Galicia."

    _enviar(u.telefono, "eso fue con santander")
    assert enviados[-1] == "¿Lo cambio de Galicia a Santander?"


def test_tc9_lote_descarta_inventadas_y_respeta_nombrada(db, monkeypatch):
    """TC9: lote sin cuentas usa Galicia en ambas y respeta cuenta si fue nombrada."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual: dict = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    actual = _resp_grabacion(
        30000.0,
        "Transporte > Combustible",
        "Nafta",
        billetera="Santander",
        billetera_origen="Santander",
        adicionales=[
            {
                "monto": 2300.0,
                "tipo": "egreso",
                "moneda": "ARS",
                "categoria": "Transporte > Peajes",
                "descripcion": "Peaje",
                "billetera": "Santander",
                "tarjeta": None,
                "fecha": None,
            }
        ],
    )
    _enviar(u.telefono, "nafta 30 lucas y peaje 2300")
    assert "desde Galicia:" in enviados[-1]
    txs_1 = db.execute(select(Transaccion).order_by(Transaccion.monto.desc())).scalars().all()
    assert len(txs_1) == 2
    assert all(t.billetera_id == esc["galicia"].id for t in txs_1)

    for tx_old in txs_1:
        db.delete(tx_old)
    for c_old in db.execute(select(ConversacionWpp)).scalars().all():
        db.delete(c_old)
    db.commit()

    actual = _resp_grabacion(
        30000.0,
        "Transporte > Combustible",
        "Nafta",
        billetera="Santander",
        billetera_origen="Santander",
        adicionales=[
            {
                "monto": 2300.0,
                "tipo": "egreso",
                "moneda": "ARS",
                "categoria": "Transporte > Peajes",
                "descripcion": "Peaje",
                "billetera": None,
                "tarjeta": None,
                "fecha": None,
            }
        ],
    )
    _enviar(u.telefono, "nafta 30 lucas con santander y peaje 2300")
    assert "Combustible · Santander" in enviados[-1]
    assert "Peajes · Galicia" in enviados[-1]
    txs_2 = db.execute(select(Transaccion)).scalars().all()
    assert len(txs_2) == 2
    nafta_2 = [t for t in txs_2 if t.monto == Decimal("30000")][0]
    peaje_2 = [t for t in txs_2 if t.monto == Decimal("2300")][0]
    assert nafta_2.billetera_id == esc["santander"].id
    assert peaje_2.billetera_id == esc["galicia"].id


def test_tc10_foto_factura_no_aplica_dc1(db, monkeypatch):
    """TC10: camino de imagen conserva la billetera resuelta por la extracción de imagen."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)

    monkeypatch.setattr(
        "app.routers.whatsapp.etapa_entrada._descargar_medio_meta",
        lambda _mid: (b"fake-img", "image/jpeg"),
    )
    res_ext = extraccion_documento.ResultadoExtraccion(
        documento_tipo="captura_actividad",
        billetera_texto="Santander",
        total_vistos=1,
        movimientos=[
            extraccion_documento.MovimientoExtraido(
                fecha=date(2026, 11, 19),
                monto=Decimal("15000"),
                moneda="ARS",
                descripcion="Edenor",
                categoria="Vivienda > Electricidad",
                sentido="egreso",
            )
        ],
        rendimientos=[],
        omitidos=[],
        vencimiento=date(2026, 11, 25),
        cuotas=[],
    )
    monkeypatch.setattr(
        "app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen",
        lambda **_kw: (res_ext, None),
    )

    _procesar_mensaje_whatsapp_background(
        {
            "msg": {
                "id": f"wamid.{uuid4().hex}",
                "from": u.telefono,
                "type": "image",
                "image": {"id": "img_m2d3", "mime_type": "image/jpeg", "caption": ""},
            },
            "wamid": f"wamid.{uuid4().hex}",
            "from_number": u.telefono,
            "msg_type": "image",
            "t_inicio": 0.0,
        }
    )

    assert "Santander" in enviados[-1]
    assert "¿Anoto $15.000 en Vivienda desde Santander?" == enviados[-1]


def test_tc11_tarjeta_inventada_vs_nombrada(db, monkeypatch):
    """TC11: tarjeta inventada se descarta (débito Galicia) y nombrada usa •••• 5077."""
    esc = _crear_usuario_m2d3(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual: dict = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    actual = _resp_grabacion(
        8000.0,
        "Alimentación > Supermercado",
        "Super",
        tarjeta="Visa Santander",
    )
    _enviar(u.telefono, "gasté 8000 en el super")
    assert enviados[-1] == "Listo, anoté $8.000 en Supermercado desde Galicia."
    tx_deb = db.execute(select(Transaccion)).scalar_one()
    assert tx_deb.billetera_id == esc["galicia"].id
    assert tx_deb.tarjeta_id is None
    assert tx_deb.metodo_pago != MetodoPago.CREDITO

    actual = _resp_grabacion(
        8000.0,
        "Alimentación > Supermercado",
        "Super",
        tarjeta="Visa Santander",
    )
    _enviar(u.telefono, "8000 en el super con la visa santander")
    assert "Visa Santander •••• 5077" in enviados[-1]
    assert "¿Lo anoto?" in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 1
