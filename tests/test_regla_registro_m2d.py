# -*- coding: utf-8 -*-
"""Pruebas de la regla de registro directo D-D2 (TD1 a TD5)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.core.config import settings
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.meta import EstadoMeta, Meta
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.usuario import (
    AuthProvider,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.routers.whatsapp import regla_registro
from app.routers.whatsapp_ia import _procesar_mensaje_whatsapp_background
from app.services import ai_service, openai_client
from app.utils.fecha import TZ_ARGENTINA
from app.utils.telefono import normalizar_telefono_ar
from tests.wpp_familias_helpers import (
    aplicar_compat_sqlite,
    crear_db_sqlite,
    instalar_enviados,
)

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _entorno_seguro(monkeypatch):
    """Compatibilidad SQLite y bloqueo total de clientes reales OpenAI/Meta."""
    aplicar_compat_sqlite(monkeypatch)

    def _prohibido(*args, **kwargs):
        raise AssertionError("Llamada real a OpenAI o Meta prohibida en tests")

    monkeypatch.setattr(openai_client, "get_openai_client", _prohibido)
    monkeypatch.setattr(ai_service, "_get_client", _prohibido)
    monkeypatch.setattr(
        "app.routers.whatsapp_ia.marcar_leido_y_escribiendo",
        lambda *_a, **_kw: None,
    )
    fijo = datetime(2026, 11, 18, 14, 0, tzinfo=TZ_ARGENTINA)
    monkeypatch.setattr("app.utils.fecha.ahora_argentina", lambda: fijo)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fijo.date())
    monkeypatch.setattr("app.routers.whatsapp.parsers.hoy_argentina", lambda: fijo.date())
    monkeypatch.setattr("app.routers.whatsapp.propuestas.hoy_argentina", lambda: fijo.date())
    monkeypatch.setattr("app.routers.whatsapp.registro.hoy_argentina", lambda: fijo.date())
    monkeypatch.setattr("app.routers.whatsapp_ia.hoy_argentina", lambda: fijo.date())


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


def _crear_usuario_completo(db: Session) -> Usuario:
    _seed_catalogo(db)
    tel = "+5491155550001"
    u = Usuario(
        id=uuid4(),
        nombre="Tester",
        apellido="M2d",
        email="tester@argentum.test",
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
    return u


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


def _respuesta_ia(
    monto: int | float,
    categoria: str,
    confianza: float = 0.95,
    tipo: str = "egreso",
    billetera: str | None = None,
    tarjeta: str | None = None,
) -> dict:
    return {
        "intent": "registrar_transaccion",
        "confianza": confianza,
        "slot_filling": False,
        "entidades": {
            "tipo": tipo,
            "monto": monto,
            "moneda": "ARS",
            "categoria": categoria,
            "descripcion": "prueba",
            "billetera": billetera,
            "billetera_origen": billetera,
            "billetera_destino": billetera if tipo == "ingreso" else None,
            "tarjeta": tarjeta,
            "fecha": None,
        },
        "datos_faltantes": [],
        "respuesta_usuario": "Listo.",
    }


def test_td1_es_mensaje_pregunta():
    """TD1: distingue preguntas reales de mensajes de movimientos con 'llegó'."""
    preguntas = [
        "¿gasté 5000 en el super?",
        "me alcanza para 50 lucas",
        "da para salir a cenar 40 lucas este finde?",
        "me banco una play de 1,2 palos?",
        "si me compro un aire de 900 lucas, llego?",
        "tengo para unas vacaciones de 2 palos?",
        "puedo gastar 50 lucas en ropa este mes?",
    ]
    no_preguntas = [
        "me llego el sueldo 900 mil",
        "me llegó la luz 21 mil",
        "gasté 5000 en el super",
        "llegué tarde y pagué taxi 6500",
        "tengo que pagar 5000 de luz",
    ]
    for p in preguntas:
        assert regla_registro.es_mensaje_pregunta(p) is True, p
    for np in no_preguntas:
        assert regla_registro.es_mensaje_pregunta(np) is False, np


def test_td2_monto_esta_en_texto():
    """TD2: valida presencia exacta de monto con jerga, palabras y separadores."""
    assert regla_registro.monto_esta_en_texto("almuerzo 9 lucas", 9000) is True
    assert regla_registro.monto_esta_en_texto("almuerzo 9 lucas", 90000) is False
    assert (
        regla_registro.monto_esta_en_texto(
            "dos mil quinientos en el colectivo", 2500
        )
        is True
    )
    frase = "nafta 30 lucas y peaje 2300"
    assert regla_registro.monto_esta_en_texto(frase, 30000) is True
    assert regla_registro.monto_esta_en_texto(frase, 2300) is True
    assert regla_registro.monto_esta_en_texto(frase, 32000) is False
    assert regla_registro.monto_esta_en_texto("gasté $19.940 en Github", 19940) is True
    assert regla_registro.monto_esta_en_texto("1,5 palos en el auto", 1500000) is True


def test_td3_categoria_adivinada():
    """TD3: descarte o 'Otros' sin subcategoría es adivinada; las demás no."""
    assert (
        regla_registro.categoria_adivinada(
            {"categoria": "Transporte", "categoria_por_descarte": True}
        )
        is True
    )
    assert regla_registro.categoria_adivinada({"categoria": "Otros"}) is True
    assert (
        regla_registro.categoria_adivinada({"categoria": "Otros › Regalos"})
        is False
    )
    assert (
        regla_registro.categoria_adivinada({"categoria": "Otros > Regalos"})
        is False
    )
    assert regla_registro.categoria_adivinada({"categoria": "Transporte"}) is False


def test_td4_flujo_completo_regla_registro(db, monkeypatch):
    """TD4: flujo completo con IA falsa verificando registro directo vs confirmación."""
    u = _crear_usuario_completo(db)
    enviados = instalar_enviados(monkeypatch)

    resp_actual = {}

    def _ia_mock(*_a, **_kw):
        return resp_actual

    monkeypatch.setattr(ai_service, "procesar_mensaje", _ia_mock)

    # 1. Almuerzo 9 lucas con monto correcto 9000 y confianza 0.95 -> anota directo
    resp_actual = _respuesta_ia(9000, "Gastronomía", confianza=0.95)
    _enviar(u.telefono, "almuerzo 9 lucas")
    assert enviados[-1].startswith("Listo, anoté $9.000")
    txs = db.execute(select(Transaccion)).scalars().all()
    assert len(txs) == 1

    # 2. Almuerzo 9 lucas pero IA inventa 90000 -> pide confirmación, 0 tx nuevas
    resp_actual = _respuesta_ia(90000, "Gastronomía", confianza=0.95)
    _enviar(u.telefono, "almuerzo 9 lucas")
    assert "¿Anoto" in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 1

    # 3. Ingreso con "me llego" no es pregunta -> anota directo
    resp_actual = _respuesta_ia(
        900000, "Sueldo", confianza=0.95, tipo="ingreso"
    )
    _enviar(u.telefono, "me llego el sueldo 900 mil")
    assert enviados[-1].startswith("Listo, anoté")
    assert len(db.execute(select(Transaccion)).scalars().all()) == 2

    # 4. Pregunta real con confianza 0.99 -> nunca anota directo
    resp_actual = _respuesta_ia(5000, "Alimentación > Supermercado", confianza=0.99)
    _enviar(u.telefono, "¿gasté 5000 en el super?")
    assert "¿Anoto" in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 2

    # 5. Categoría por descarte con confianza 0.99 -> pide confirmación
    resp_actual = _respuesta_ia(5000, "CategoriaInventadaXYZ", confianza=0.99)
    _enviar(u.telefono, "gasté 5000 en kosovo")
    assert "¿Anoto" in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 2

    # 6. Confianza 0.70 con UMBRAL 0.85 -> pide confirmación
    monkeypatch.setattr(settings, "UMBRAL_CONFIANZA_IA", 0.85)
    resp_actual = _respuesta_ia(7000, "Gastronomía", confianza=0.70)
    _enviar(u.telefono, "merienda 7 lucas")
    assert "¿Anoto" in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 2

    # 7. Misma con UMBRAL=0.60 via monkeypatch -> anota directo
    monkeypatch.setattr(settings, "UMBRAL_CONFIANZA_IA", 0.60)
    resp_actual = _respuesta_ia(7000, "Gastronomía", confianza=0.70)
    _enviar(u.telefono, "merienda 7 lucas")
    assert enviados[-1].startswith("Listo, anoté")
    assert len(db.execute(select(Transaccion)).scalars().all()) == 3

    # 8. Compra con tarjeta en 1 pago y confianza 0.99 -> sigue preguntando
    monkeypatch.setattr(settings, "UMBRAL_CONFIANZA_IA", 0.85)
    resp_actual = _respuesta_ia(
        4000, "Gastronomía", confianza=0.99, tarjeta="Visa Galicia"
    )
    _enviar(u.telefono, "helado 4 lucas con la visa galicia")
    assert "¿Anoto" in enviados[-1]
    assert len(db.execute(select(Transaccion)).scalars().all()) == 3


def test_td5_control_estatico_umbral_confianza():
    """TD5: sin '< 0.85' en etapa_ia/etapa_resolucion y 4 llamadas a confianza_ia_baja."""
    ia_txt = (BACKEND / "app" / "routers" / "whatsapp" / "etapa_ia.py").read_text(
        encoding="utf-8"
    )
    res_txt = (
        BACKEND / "app" / "routers" / "whatsapp" / "etapa_resolucion.py"
    ).read_text(encoding="utf-8")
    assert "confianza_ia_raw < 0.85" not in ia_txt
    assert "confianza_ia_raw < 0.85" not in res_txt
    total_llamadas = ia_txt.count("confianza_ia_baja(") + res_txt.count(
        "confianza_ia_baja("
    )
    assert total_llamadas == 4
    assert settings.UMBRAL_CONFIANZA_IA == 0.70
