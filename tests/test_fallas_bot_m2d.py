# -*- coding: utf-8 -*-
"""Pruebas de regresión para fallas del bot D-F1 a D-F16 (TF1 a TF16)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp
from app.models.meta import EstadoMeta, Meta
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import (
    AuthProvider,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
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
    fijo = datetime(2026, 11, 18, 14, 0, tzinfo=TZ_ARGENTINA)
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
        "app.routers.whatsapp.metas",
        "app.routers.whatsapp.transferencias",
        "app.routers.whatsapp.transferencias_flujo",
        "app.routers.whatsapp.transferencias_flujo_b",
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


def _crear_usuario_completo(db: Session) -> dict:
    _seed_catalogo(db)
    tel = "+5491155550002"
    u = Usuario(
        id=uuid4(),
        nombre="Tester",
        apellido="Fallas",
        email="fallas@argentum.test",
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
        es_principal=True,
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
    m1 = Meta(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Fondo de Emergencia",
        monto_objetivo=Decimal("500000"),
        monto_actual=Decimal("50000"),
        moneda=Moneda.ARS,
        estado=EstadoMeta.ACTIVA,
    )
    m2 = Meta(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Viaje a Bariloche",
        monto_objetivo=Decimal("1000000"),
        monto_actual=Decimal("100000"),
        moneda=Moneda.ARS,
        estado=EstadoMeta.ACTIVA,
    )
    db.add_all([t1, t2, t3, m1, m2])
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
        "meta_fondo": m1,
        "meta_bariloche": m2,
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


def _resp_tx(
    monto: int | float,
    categoria: str,
    tipo: str = "egreso",
    confianza: float = 0.95,
    moneda: str = "ARS",
    billetera: str | None = None,
    tarjeta: str | None = None,
    cuotas: int | None = None,
) -> dict:
    return {
        "intent": "registrar_transaccion",
        "confianza": confianza,
        "slot_filling": False,
        "entidades": {
            "tipo": tipo,
            "monto": monto,
            "moneda": moneda,
            "categoria": categoria,
            "descripcion": "test",
            "billetera": billetera,
            "billetera_origen": billetera if tipo == "egreso" else None,
            "billetera_destino": billetera if tipo == "ingreso" else None,
            "tarjeta": tarjeta,
            "cantidad_cuotas": cuotas,
            "fecha": None,
        },
        "datos_faltantes": [],
        "respuesta_usuario": "Listo.",
    }


def test_tf1_b098_b102_b126_b373_cuota_sin_numero(db, monkeypatch):
    """TF1: 'cuota' sin número es gasto común en Galicia; con número+tarjeta sigue tarjeta."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)

    casos = [
        ("B098", "cuota del club 25 lucas", 25000, "Entretenimiento"),
        ("B102", "cuota de la facu 80 mil", 80000, "Educación"),
        ("B126", "cuota del préstamo 85 mil", 85000, "Banco > Préstamos"),
        ("B373", "cuota del colegio 120 mil", 120000, "Educación"),
    ]
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    for _fid, msg, monto, cat in casos:
        actual = _resp_tx(monto, cat)
        _enviar(u.telefono, msg)
        assert "tarjeta" not in enviados[-1].lower()
        assert "cuota" not in enviados[-1].lower() or "anoté" in enviados[-1].lower()

    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 4
    for tx in txs:
        assert tx.billetera_id == esc["galicia"].id
        assert tx.tarjeta_id is None

    # Caso con número y tarjeta sigue yendo a propuesta de tarjeta
    actual = _resp_tx(30000, "Compras", cuotas=3, tarjeta="Visa Santander")
    _enviar(u.telefono, "3 cuotas de 10 mil con la visa santander")
    assert "¿Las anoto?" in enviados[-1]
    assert "5077" in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 4


def test_tf2_b150_b157_b159_b160_b192_b199_b380_banco_cuenta_vs_tarjeta(
    db, monkeypatch
):
    """TF2: nombre de banco sin palabra de tarjeta usa la billetera (egreso/ingreso)."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    egresos_sant = [
        (
            "B150",
            "4.500 de santander, compras supermercado",
            4500,
            "Alimentación > Supermercado",
            "Santander",
        ),
        ("B151", "almuerzo $8000 con santander", 8000, "Gastronomía", None),
        ("B152", "pagué 7000 con el rio en la farmacia", 7000, "Salud > Farmacia", None),
        (
            "B157",
            "egreso de 15.300 de santander uber",
            15300,
            "Transporte > Taxi y apps",
            None,
        ),
        (
            "B158",
            "10.588 en supermercado con santander",
            10588,
            "Alimentación > Supermercado",
            "Santander",
        ),
        (
            "B160",
            "pagué la luz desde santander, 21 mil",
            21000,
            "Vivienda > Electricidad",
            "Santander",
        ),
        (
            "B169",
            "almuerzo de ayer 11 mil con santander",
            11000,
            "Gastronomía",
            "Santander",
        ),
    ]
    for _fid, msg, monto, cat, bill_ia in egresos_sant:
        actual = _resp_tx(monto, cat, billetera=bill_ia)
        _enviar(u.telefono, msg)
        assert "Santander" in enviados[-1]
        assert "5077" not in enviados[-1]
        assert "¿Fue con la cuenta" not in enviados[-1]

    # B159 -> Galicia sin preguntar tarjeta
    actual = _resp_tx(2500, "Alimentación", billetera=None)
    _enviar(u.telefono, "pan 2500 desde gali")
    assert "Galicia" in enviados[-1]
    assert "¿Fue con la cuenta" not in enviados[-1]
    assert "1506" not in enviados[-1]

    # B192, B199, B380 -> ingreso en Santander (incluso cuando IA omite billetera_destino)
    ingresos_sant = [
        ("B192", "me cayó la jubilación 380000 en el santander", 380000, "Jubilación y AUH"),
        ("B199", "ingreso de $8.000 en santander", 8000, "Trabajo > Ventas"),
        ("B380", "ingreso 15 lucas en santander de una venta", 15000, "Trabajo > Ventas"),
    ]
    for _fid, msg, monto, cat in ingresos_sant:
        actual = _resp_tx(monto, cat, tipo="ingreso", billetera=None)
        _enviar(u.telefono, msg)
        assert "Santander" in enviados[-1]

    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 11
    sant_txs = [t for t in txs if t.billetera_id == esc["santander"].id]
    gal_txs = [t for t in txs if t.billetera_id == esc["galicia"].id]
    assert len(sant_txs) == 10
    assert len(gal_txs) == 1

    # Con palabra de tarjeta ("visa") -> sigue siendo tarjeta •••• 5077
    actual = _resp_tx(4500, "Alimentación", tarjeta="Visa Santander")
    _enviar(u.telefono, "4500 con la visa santander")
    assert "5077" in enviados[-1]
    assert "¿Lo anoto?" in enviados[-1]


def test_tf3_b161_b162_b323_b381_b384_efectivo_segun_moneda(db, monkeypatch):
    """TF3: efectivo en todas sus formas elige Efectivo Pesos o Dólares según moneda."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    casos_ars = [
        ("B005", "café 2500 en efectivo", 2500, "Gastronomía", "Efectivo"),
        ("B153", "kiosco 3000 en efectivo", 3000, "Alimentación > Kiosco", "Efectivo"),
        ("B154", "verdulería 6 lucas cash", 6000, "Alimentación > Verdulería", "cash"),
        ("B161", "helado 4000 con plata física", 4000, "Gastronomía", None),
        ("B162", "taxi 6500 en efectivo", 6500, "Transporte > Taxi y apps", None),
        ("B381", "nafta 25 mil en efectivo", 25000, "Transporte > Combustible", "Efectivo"),
        (
            "B384",
            "pagué 8 mil de la verdu con billetes",
            8000,
            "Alimentación > Verdulería",
            None,
        ),
    ]
    for _fid, msg, monto, cat, bill_ia in casos_ars:
        actual = _resp_tx(monto, cat, billetera=bill_ia)
        _enviar(u.telefono, msg)
        assert "Efectivo Pesos" in enviados[-1]
        assert "Efectivo Dólares" not in enviados[-1]

    # 60 dólares en efectivo -> Efectivo Dólares
    actual = _resp_tx(60, "Gastronomía", moneda="USD", billetera=None)
    _enviar(u.telefono, "60 dólares en efectivo")
    assert "Efectivo Dólares" in enviados[-1]

    # B323: "gasté 9000 en uber" -> "fue en efectivo" -> cambia a Efectivo Pesos
    actual = _resp_tx(9000, "Transporte > Taxi y apps", billetera="Galicia")
    _enviar(u.telefono, "gasté 9000 en uber")
    assert "Galicia" in enviados[-1]
    actual = {"intent": "corregir", "confianza": 0.95, "slot_filling": False, "entidades": {}}
    _enviar(u.telefono, "fue en efectivo")
    assert "Efectivo Dólares es en dólares" not in enviados[-1]
    assert "Efectivo Pesos" in enviados[-1]

    actual = {"intent": "confirmar", "confianza": 0.95, "slot_filling": False, "entidades": {}}
    _enviar(u.telefono, "sí")
    uber_tx = (
        db.execute(
            select(Transaccion).where(Transaccion.monto == Decimal("9000"))
        )
        .scalars()
        .first()
    )
    assert uber_tx is not None
    assert uber_tx.billetera_id == esc["efectivo_ars"].id


def test_tf4_b335_b339_b376_metas_con_jerga(db, monkeypatch):
    """TF4: montos con jerga ('lucas') en aportes a metas se interpretan correctamente."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)

    _enviar(u.telefono, "aporté 20 lucas a la meta de bariloche")
    assert "$20.000" in enviados[-1]
    assert "Viaje a Bariloche" in enviados[-1]

    _enviar(u.telefono, "mandé 25 lucas a la meta de bariloche desde santander")
    assert "$25.000" in enviados[-1]
    assert "Santander" in enviados[-1]

    _enviar(u.telefono, "guardé 30 lucas en la meta del viaje a bariloche")
    assert "$30.000" in enviados[-1]
    assert "Viaje a Bariloche" in enviados[-1]


def test_tf5_b336_b377_aporte_sin_palabra_meta(db, monkeypatch):
    """TF5: verbo de ahorro + monto + nombre de meta activa propone el aporte; sin meta no."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)

    _enviar(u.telefono, "puse 50 mil para el fondo de emergencia")
    assert "¿Aporto $50.000 a tu meta Fondo de Emergencia desde Galicia?" == enviados[-1]

    _enviar(u.telefono, "separé 40 mil para el fondo de emergencia")
    assert "¿Aporto $40.000 a tu meta Fondo de Emergencia desde Galicia?" == enviados[-1]

    # Sin meta nombrada ('puse 50 mil') no debe responder como meta; pasa a IA
    llamadas_ia = []

    def _ia_slot(*_a, **_kw):
        llamadas_ia.append(1)
        return {
            "intent": "slot_filling",
            "confianza": 0.90,
            "slot_filling": True,
            "entidades": {"monto": 50000},
            "datos_faltantes": ["tipo"],
            "respuesta_usuario": "¿Fue un gasto, ingreso o transferencia?",
        }

    monkeypatch.setattr(ai_service, "procesar_mensaje", _ia_slot)
    _enviar(u.telefono, "puse 50 mil")
    assert len(llamadas_ia) == 1
    assert "meta" not in enviados[-1].lower()


def test_tf6_b041_a_b045_b141_b382_b383_transporte_publico(db, monkeypatch):
    """TF6: transporte público resuelve Transporte › Transporte público."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    # Incluye tanto "Transporte" (como P7.5 #0 'gasté 4000 en el bondi') como "Transporte público"
    casos = [
        ("B041", "colectibo 1200", 1200, "Transporte"),
        ("B042", "cargué 10 lucas en la sube", 10000, "Transporte"),
        ("B043", "gasté 5.445 en subte", 5445, "Transporte público"),
        ("B044", "24410 en pasajes de cole", 24410, "Transporte"),
        ("B045", "10000 en trasporte publico", 10000, "Transporte"),
        ("B141", "dos mil quinientos en el colectivo", 2500, "Transporte público"),
        ("B382", "subte 1200", 1200, "Transporte"),
        ("B383", "tren 900", 900, "Transporte"),
    ]
    cat_tr = (
        db.execute(select(Categoria).where(Categoria.nombre == "Transporte"))
        .scalars()
        .first()
    )
    sub_tp = (
        db.execute(
            select(Subcategoria).where(Subcategoria.nombre == "Transporte público")
        )
        .scalars()
        .first()
    )
    for _fid, msg, monto, cat_ia in casos:
        # Cada frase se evalúa de forma independiente sin choque de duplicado
        for prev in db.execute(select(Transaccion)).scalars().all():
            db.delete(prev)
        db.commit()
        actual = _resp_tx(monto, cat_ia)
        _enviar(u.telefono, msg)
        assert "Transporte público" in enviados[-1]
        txs = db.execute(select(Transaccion)).scalars().all()
        assert len(txs) == 1
        assert txs[0].categoria_id == cat_tr.id
        assert txs[0].subcategoria_id == sub_tp.id


def test_tf7_b328_correccion_monto_dos_montos_y_herencia(db, monkeypatch):
    """TF7: 'no era 10, eran 15' hereda 'lucas' -> 15.000; '15000' -> 'no, 1500' queda 1.500."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    # 1. B328: "gasté 10 lucas en un regalo" -> "no era 10, eran 15" -> confirma "sí" -> 15.000
    actual = _resp_tx(10000, "Otros > Regalos")
    _enviar(u.telefono, "gasté 10 lucas en un regalo")
    assert "Listo, anoté $10.000" in enviados[-1]

    _enviar(u.telefono, "no era 10, eran 15")
    assert "$15.000" in enviados[-1]
    assert "¿Lo cambio" in enviados[-1] or "¿Lo dejo" in enviados[-1]

    actual = {"intent": "confirmar", "confianza": 0.95, "slot_filling": False, "entidades": {}}
    _enviar(u.telefono, "sí")
    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 1
    assert txs[0].monto == Decimal("15000")

    # 2. "15000 en la verdu" + "no, 1500" -> sin jerga original queda 1.500
    actual = _resp_tx(15000, "Alimentación > Verdulería")
    _enviar(u.telefono, "15000 en la verdu")
    _enviar(u.telefono, "no, 1500")
    assert "$1.500" in enviados[-1]
    _enviar(u.telefono, "sí")
    verdu = (
        db.execute(
            select(Transaccion).where(Transaccion.monto == Decimal("1500"))
        )
        .scalars()
        .all()
    )
    assert len(verdu) == 1


def test_tf8_b234_b235_b243_b374_b375_cuotas_total_vs_cada_una(db, monkeypatch):
    """TF8: distingue total vs cada cuota en todas sus variantes y pregunta solo si ambiguo."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    casos = [
        (
            "B234",
            "celular en 6 cuotas sin interés de 25000 con la amex",
            25000,
            "Equipos",
            ["6 cuotas de $25.000", "($150.000)"],
        ),
        (
            "B235",
            "compré pasajes en 6 cuotas, 300 lucas en total, con la visa del galicia",
            300000,
            "Transporte",
            ["6 cuotas", "($300.000)", "1506"],
        ),
        (
            "B243",
            "gasté 100 mil en la amex en 4 cuotas en el super",
            100000,
            "Alimentación > Supermercado",
            ["4 cuotas de $25.000", "($100.000)"],
        ),
        (
            "B374",
            "200 lucas en 6 cuotas con la visa santander, un celular",
            200000,
            "Equipos",
            ["6 cuotas", "($200.000)", "5077"],
        ),
        (
            "B375",
            "6 cuotas de 30 mil con la amex, unas zapatillas",
            30000,
            "Calzado",
            ["6 cuotas de $30.000", "($180.000)"],
        ),
        (
            "B232",
            "zapatillas en 3 cuotas de 40 lucas con la amex",
            40000,
            "Calzado",
            ["3 cuotas de $40.000", "($120.000)"],
        ),
        (
            "B236",
            "notebook 18 cuotas de 85 mil con la amex",
            85000,
            "Equipos",
            ["18 cuotas de $85.000", "($1.530.000)"],
        ),
        (
            "B237",
            "colchón en 3 pagos de 100 mil con la amex",
            100000,
            "Hogar > Muebles y electro",
            ["3 cuotas de $100.000", "($300.000)"],
        ),
    ]
    for _fid, msg, monto, cat, esperados in casos:
        # Cada frase de cuota es independiente de propuestas pendientes previas
        for prev in db.execute(select(ConversacionWpp)).scalars().all():
            prev.accion_ejecutada = "test_reset"
            prev.slot_filling_activo = False
        db.commit()
        actual = _resp_tx(monto, cat)
        _enviar(u.telefono, msg)
        for esp in esperados:
            assert esp in enviados[-1], (msg, enviados[-1])

    # "gasté 80000 en 12 cuotas" con Amex -> total 80.000
    for prev in db.execute(select(ConversacionWpp)).scalars().all():
        prev.accion_ejecutada = "test_reset"
        prev.slot_filling_activo = False
    db.commit()
    actual = _resp_tx(80000, "Compras", tarjeta="Amex Galicia")
    _enviar(u.telefono, "gasté 80000 en 12 cuotas con la amex")
    assert "($80.000)" in enviados[-1]

    # Ambiguo sin ninguna forma -> pregunta si es total o cada cuota
    for prev in db.execute(select(ConversacionWpp)).scalars().all():
        prev.accion_ejecutada = "test_reset"
        prev.slot_filling_activo = False
    db.commit()
    actual = _resp_tx(120000, "Indumentaria", tarjeta="Amex Galicia")
    _enviar(u.telefono, "campera 120 lucas, 6 cuotas con la amex")
    assert "¿Los $120.000 son el total o cada cuota?" == enviados[-1]


def test_tf9_b259_b379_compra_dolares_con_tarjeta(db, monkeypatch):
    """TF9: compra en dólares con tarjeta respeta moneda USD y conserva tarjeta ARS."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    # B259: "gasté 20 dólares con la amex en una app"
    actual = _resp_tx(20, "Tecnología > Apps y programas", moneda="USD")
    _enviar(u.telefono, "gasté 20 dólares con la amex en una app")
    assert "US$20" in enviados[-1]
    assert "2745" in enviados[-1]
    assert "en un pago" in enviados[-1]

    actual = {"intent": "confirmar", "confianza": 0.95, "slot_filling": False, "entidades": {}}
    _enviar(u.telefono, "sí")
    assert "US$20" in enviados[-1]

    # B379: "gasté 50 usd con la visa santander en un hotel"
    actual = _resp_tx(50, "Ocio > Viajes y turismo", moneda="USD")
    _enviar(u.telefono, "gasté 50 usd con la visa santander en un hotel")
    assert "US$50" in enviados[-1]
    assert "5077" in enviados[-1]
    actual = {"intent": "confirmar", "confianza": 0.95, "slot_filling": False, "entidades": {}}
    _enviar(u.telefono, "sí")
    assert "US$50" in enviados[-1]

    txs = (
        db.execute(
            select(Transaccion)
            .where(Transaccion.es_cuota_hija == False)
            .order_by(Transaccion.monto.asc())
        )
        .scalars()
        .all()
    )
    assert len(txs) == 2
    assert txs[0].monto == Decimal("20")
    assert txs[0].moneda == Moneda.USD
    assert txs[0].tarjeta_id == esc["amex_galicia"].id
    assert txs[1].monto == Decimal("50")
    assert txs[1].moneda == Moneda.USD
    assert txs[1].tarjeta_id == esc["visa_santander"].id
    assert esc["amex_galicia"].moneda == Moneda.ARS
    assert esc["visa_santander"].moneda == Moneda.ARS


def test_tf10_b261_b378_compra_dolares_con_cotizacion(db, monkeypatch):
    """TF10: 'a <cotiz>' luego del banco detecta la cotización sin volver a preguntar."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)

    # B261: "compré 300 usd con el santander a 1470"
    _enviar(u.telefono, "compré 300 usd con el santander a 1470")
    assert "¿A cuánto compraste" not in enviados[-1]
    assert "Santander" in enviados[-1]
    assert "Efectivo Dólares" in enviados[-1]
    assert "1.470" in enviados[-1]
    assert "US$300" in enviados[-1]
    _enviar(u.telefono, "sí")

    # B378: "compré 200 dólares con galicia a 1480"
    _enviar(u.telefono, "compré 200 dólares con galicia a 1480")
    assert "Galicia" in enviados[-1]
    assert "Efectivo Dólares" in enviados[-1]
    assert "1.480" in enviados[-1]
    assert "US$200" in enviados[-1]
    _enviar(u.telefono, "sí")

    transfs = (
        db.execute(
            select(TransferenciaInterna).order_by(
                TransferenciaInterna.monto_destino.desc()
            )
        )
        .scalars()
        .all()
    )
    assert len(transfs) == 2
    assert transfs[0].billetera_origen_id == esc["santander"].id
    assert transfs[0].billetera_destino_id == esc["efectivo_usd"].id
    assert transfs[0].monto_destino == Decimal("300")
    assert transfs[1].billetera_origen_id == esc["galicia"].id
    assert transfs[1].billetera_destino_id == esc["efectivo_usd"].id
    assert transfs[1].monto_destino == Decimal("200")


def test_tf11_b318_b321_b322_correccion_y_anulacion_sobre_propuesta(
    db, monkeypatch
):
    """TF11: corrección/anulación dentro de 30m aplica a la propuesta pendiente."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    actual = {}
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: actual)

    # B318: "15000 en la verdu" (conf=0.70 -> propuesta) -> "no, 1500" -> "sí"
    actual = _resp_tx(15000, "Alimentación > Verdulería", confianza=0.70)
    _enviar(u.telefono, "15000 en la verdu")
    assert "¿Anoto $15.000 en Verdulería desde Galicia?" == enviados[-1]

    _enviar(u.telefono, "no, 1500")
    assert "¿Anoto $1.500 en Verdulería desde Galicia?" == enviados[-1]
    assert "No anotaste nada" not in enviados[-1]

    _enviar(u.telefono, "sí")
    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 1
    assert txs[0].monto == Decimal("1500")

    # B321: "cobré 300 mil de sueldo" (conf=0.70) -> "eran 350 mil" -> "sí"
    actual = _resp_tx(300000, "Sueldo", tipo="ingreso", confianza=0.70)
    _enviar(u.telefono, "cobré 300 mil de sueldo")
    assert "¿Anoto" in enviados[-1] and "$300.000" in enviados[-1]

    _enviar(u.telefono, "eran 350 mil")
    assert "¿Anoto" in enviados[-1] and "$350.000" in enviados[-1]

    _enviar(u.telefono, "sí")
    ing = (
        db.execute(
            select(Transaccion).where(Transaccion.monto == Decimal("350000"))
        )
        .scalars()
        .all()
    )
    viejo = (
        db.execute(
            select(Transaccion).where(Transaccion.monto == Decimal("300000"))
        )
        .scalars()
        .all()
    )
    assert len(ing) == 1
    assert len(viejo) == 0

    # B322: "nafta 40 lucas" (conf=0.70) -> "anulá eso" -> "Listo, no lo anoto." -> "sí"
    actual = _resp_tx(40000, "Transporte > Combustible", confianza=0.70)
    _enviar(u.telefono, "nafta 40 lucas")
    assert "¿Anoto $40.000" in enviados[-1]

    _enviar(u.telefono, "anulá eso")
    assert enviados[-1] == "Listo, no lo anoto."

    _enviar(u.telefono, "sí")
    assert any(
        fr in enviados[-1]
        for fr in ("No tengo nada pendiente", "Ya lo había anotado", "Ya estaba anotado")
    )
    assert len(db.execute(select(Transaccion)).scalars().all()) == 2
    assert (
        db.execute(
            select(Transaccion).where(Transaccion.monto == Decimal("40000"))
        )
        .scalars()
        .first()
        is None
    )

    # Extras: "fue con santander", "eso era kiosco", y "cancelá" -> "Listo, no lo anoto."
    actual = _resp_tx(5000, "Alimentación > Verdulería", confianza=0.70)
    _enviar(u.telefono, "gasté 5000 en la verdu")
    _enviar(u.telefono, "fue con santander")
    assert "Santander" in enviados[-1] and "¿Anoto" in enviados[-1]
    _enviar(u.telefono, "eso era kiosco")
    assert "Kiosco" in enviados[-1] and "¿Anoto" in enviados[-1]
    _enviar(u.telefono, "cancelá")
    assert enviados[-1] == "Listo, no lo anoto."


def test_tf12_b178_cantidad_cuotas_none_en_lote(db, monkeypatch):
    """TF12: lote con cantidad_cuotas=None no crashea y anota los movimientos."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)

    resp_lote = {
        "intent": "registrar_transaccion",
        "confianza": 0.95,
        "slot_filling": False,
        "entidades": {
            "tipo": "egreso",
            "monto": 5000,
            "moneda": "ARS",
            "categoria": "Alimentación > Kiosco",
            "descripcion": "kiosco",
            "billetera": "Galicia",
            "billetera_origen": "Galicia",
            "tarjeta": None,
            "cantidad_cuotas": None,
            "transacciones_adicionales": [
                {
                    "tipo": "egreso",
                    "monto": 30000,
                    "moneda": "ARS",
                    "categoria": "Calzado",
                    "descripcion": "zapatillas",
                    "tarjeta": "Amex Galicia",
                    "cantidad_cuotas": None,
                }
            ],
        },
        "datos_faltantes": [],
        "respuesta_usuario": "Listo.",
    }
    monkeypatch.setattr(ai_service, "procesar_mensaje", lambda *_a, **_kw: resp_lote)

    _enviar(u.telefono, "gasté 5000 en el kiosco y 30000 en zapatillas con la amex")
    assert "Hubo un problema" not in enviados[-1]
    if "¿Anoto" in enviados[-1]:
        _enviar(u.telefono, "sí")
    assert "Hubo un problema" not in enviados[-1]
    assert "anoté" in enviados[-1].lower()
    txs = (
        db.execute(select(Transaccion).where(Transaccion.es_cuota_hija == False))
        .scalars()
        .all()
    )
    assert len(txs) == 2


def test_tf13_b279_b281_b282_b283_b370_b371_me_lo_puedo_permitir(
    db, monkeypatch
):
    """TF13: preguntas de FRASES_PREGUNTA_PLATA / 'llego' van a puede_permitirse sin IA."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    llamadas_ia = []
    monkeypatch.setattr(
        ai_service,
        "procesar_mensaje",
        lambda *_a, **_kw: llamadas_ia.append(1) or {},
    )

    frases = [
        ("B279", "da para salir a cenar 40 lucas este finde?"),
        ("B281", "me banco una play de 1,2 palos?"),
        ("B282", "si me compro un aire de 900 lucas, llego?"),
        ("B283", "tengo para unas vacaciones de 2 palos?"),
        ("B370", "si me compro una bici de 300 lucas me alcanza?"),
        ("B371", "puedo gastar 50 lucas en ropa este mes?"),
    ]
    for _fid, msg in frases:
        _enviar(u.telefono, msg)
        assert len(enviados[-1]) > 0

    assert len(llamadas_ia) == 0
    assert len(db.execute(select(Transaccion)).scalars().all()) == 0
    convs = db.execute(select(ConversacionWpp)).scalars().all()
    assert len(convs) == len(frases)
    for c in convs:
        assert c.intent_detectado == "puede_permitirse"


def test_tf14_b293_cuotas_vs_contado_contra(db, monkeypatch):
    """TF14: 'contra 900k' detecta cuotas_vs_contado con N=24, X=50.000, Y=900.000 sin IA."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)
    llamadas_ia = []
    monkeypatch.setattr(
        ai_service,
        "procesar_mensaje",
        lambda *_a, **_kw: llamadas_ia.append(1) or {},
    )

    _enviar(u.telefono, "¿cuotas o contado? 24 cuotas de 50k contra 900k")
    assert len(llamadas_ia) == 0
    conv = db.execute(select(ConversacionWpp)).scalars().first()
    assert conv is not None
    assert conv.intent_detectado == "cuotas_vs_contado"
    assert conv.entidades["cantidad_cuotas"] == 24
    assert Decimal(str(conv.entidades["valor_cuota"])) == Decimal("50000")
    assert Decimal(str(conv.entidades["precio_contado"])) == Decimal("900000")
    assert len(enviados[-1]) > 0


def test_tf15_b257_b369_ambiguedad_suscripcion_y_gasto_suelto(db, monkeypatch):
    """TF15: guarda monto/moneda; 'sí' propone mensual directo; 'no, fue una vez' anota gasto."""
    esc = _crear_usuario_completo(db)
    u = esc["usuario"]
    enviados = instalar_enviados(monkeypatch)

    # B257: "pagué 20 dolares de chatgpt" -> "sí" -> propuesta mensual directa (0 movimientos)
    _enviar(u.telefono, "pagué 20 dolares de chatgpt")
    assert "¿ChatGPT lo pagás todos los meses?" == enviados[-1]
    _enviar(u.telefono, "sí")
    assert "US$20 por mes" in enviados[-1]
    assert "¿Anoto la suscripción?" in enviados[-1]
    assert "¿Cada cuánto se paga" not in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 0

    # B369: "pagué 20 dolares de chatgpt" -> "no, fue una vez" -> 1 egreso US$20 sin repreguntar
    _enviar(u.telefono, "pagué 20 dolares de chatgpt")
    _enviar(u.telefono, "no, fue una vez")
    assert "¿Cuánto gastaste" not in enviados[-1]
    assert "US$20" in enviados[-1]
    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 1
    assert txs[0].monto == Decimal("20")
    assert txs[0].moneda == Moneda.USD

    # Sin monto guardado: "pagué el youtube premium" + "sí" -> pregunta frecuencia como hoy
    _enviar(u.telefono, "pagué el youtube premium")
    _enviar(u.telefono, "sí")
    assert "¿Cada cuánto se paga" in enviados[-1]
