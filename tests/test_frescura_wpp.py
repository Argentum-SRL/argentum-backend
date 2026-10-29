# -*- coding: utf-8 -*-
"""
tests/test_frescura_wpp.py - Pruebas del aviso de frescura en WhatsApp (fase_g2_saldo_confianza).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.aviso_frescura_wpp import linea_frescura_wpp
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.services.frescura_service import Frescura
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


# Setup SQLite compatibility para pruebas unitarias
@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid_processor = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    usuario = Usuario(
        id=uuid4(),
        email="test_frescura_wpp@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def test_linea_frescura_wpp_unitario():
    """
    linea_frescura_wpp:
    - dias_sin_cargar 13 (con datos_viejos True) → "Ojo: hace 13 días que no cargás nada, puede no estar al día."
    - con 9999 → largo <= 90
    - sin datos viejos → None
    """
    # 1. 13 días
    f_13 = Frescura(
        ultima_carga=date(2026, 10, 20),
        dias_sin_cargar=13,
        umbral_dias=7,
        intervalos=3,
        datos_viejos=True,
    )
    res_13 = linea_frescura_wpp(f_13)
    assert res_13 == "Ojo: hace 13 días que no cargás nada, puede no estar al día."

    # 2. 9999 días
    f_9999 = Frescura(
        ultima_carga=date(1999, 1, 1),
        dias_sin_cargar=9999,
        umbral_dias=7,
        intervalos=3,
        datos_viejos=True,
    )
    res_9999 = linea_frescura_wpp(f_9999)
    assert res_9999 is not None
    assert len(res_9999) <= 90

    # 3. Sin datos viejos
    f_al_dia = Frescura(
        ultima_carga=date(2026, 11, 1),
        dias_sin_cargar=1,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=False,
    )
    assert linea_frescura_wpp(f_al_dia) is None


def test_consultar_saldo_wpp_con_datos_viejos_y_al_dia(db: Session, usuario_test: Usuario):
    """
    Usuario con billetera en pesos y transacciones MANUAL creadas en hoy − 20, − 18, − 15 y − 13:
    la última línea de la respuesta de "consultar_saldo" es "Ojo: hace 13 días que no cargás nada, puede no estar al día."
    y el resto es igual a la respuesta del mismo usuario con linea_frescura_wpp parcheada para devolver None.
    Con una carga en hoy − 2: la respuesta no contiene "Ojo: hace".
    """
    hoy = hoy_argentina()

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=hoy - timedelta(days=30),
    )
    cat = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([billetera, cat])
    db.commit()

    # Transacciones en hoy - 20, - 18, - 15 y - 13 (a las 12:00 AR)
    fechas_dias = [20, 18, 15, 13]
    for d in fechas_dias:
        dia_dt = datetime.combine(hoy - timedelta(days=d), datetime.min.time(), tzinfo=TZ_ARGENTINA).replace(hour=12)
        tx = Transaccion(
            id=uuid4(),
            usuario_id=usuario_test.id,
            billetera_id=billetera.id,
            categoria_id=cat.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("1000.00"),
            moneda=Moneda.ARS,
            origen=OrigenTransaccion.MANUAL,
            fecha=dia_dt.date(),
            fecha_creacion=dia_dt,
            metodo_pago=MetodoPago.TRANSFERENCIA,
        )
        db.add(tx)
    db.commit()

    # Ejecutar consultar_saldo normal
    res_normal: dict = {}
    enriquecer_respuesta_por_intent(
        intent_detectado="consultar_saldo",
        resultado_ia=res_normal,
        mensaje_texto="saldo",
        usuario=usuario_test,
        db=db,
    )
    texto_normal = res_normal.get("respuesta_usuario", "")
    lineas = texto_normal.splitlines()
    assert lineas[-1] == "Ojo: hace 13 días que no cargás nada, puede no estar al día."

    # Ejecutar con linea_frescura_wpp parcheada para devolver None
    with patch("app.routers.whatsapp.aviso_frescura_wpp.linea_frescura_wpp", return_value=None):
        res_mock: dict = {}
        enriquecer_respuesta_por_intent(
            intent_detectado="consultar_saldo",
            resultado_ia=res_mock,
            mensaje_texto="saldo",
            usuario=usuario_test,
            db=db,
        )
        texto_mock = res_mock.get("respuesta_usuario", "")

    # El resto debe ser igual
    resto_normal = "\n".join(lineas[:-1])
    assert resto_normal == texto_mock

    # Agregar carga en hoy - 2
    dia_reciente = datetime.combine(hoy - timedelta(days=2), datetime.min.time(), tzinfo=TZ_ARGENTINA).replace(hour=12)
    tx_reciente = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        fecha=dia_reciente.date(),
        fecha_creacion=dia_reciente,
        metodo_pago=MetodoPago.TRANSFERENCIA,
    )
    db.add(tx_reciente)
    db.commit()

    res_al_dia: dict = {}
    enriquecer_respuesta_por_intent(
        intent_detectado="consultar_saldo",
        resultado_ia=res_al_dia,
        mensaje_texto="saldo",
        usuario=usuario_test,
        db=db,
    )
    assert "Ojo: hace" not in res_al_dia.get("respuesta_usuario", "")


def test_consultar_saldo_wpp_fallos_controlados(db: Session, usuario_test: Usuario):
    """
    - Con datos viejos y _calcular_saldo_disponible_sync fallando: la respuesta es exactamente
      "No pude consultar tu saldo en este momento. Probá de nuevo en unos minutos.".
    - Con calcular_frescura fallando: la respuesta es la de siempre, sin la línea.
    """
    hoy = hoy_argentina()

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Caja",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=hoy - timedelta(days=40),
    )
    db.add(billetera)
    db.commit()

    # 1. Fallo en _calcular_saldo_disponible_sync
    with patch(
        "app.services.contexto_financiero_service._calcular_saldo_disponible_sync",
        side_effect=RuntimeError("Error simulado de saldo"),
    ):
        res_fallo_sync: dict = {}
        enriquecer_respuesta_por_intent(
            intent_detectado="consultar_saldo",
            resultado_ia=res_fallo_sync,
            mensaje_texto="saldo",
            usuario=usuario_test,
            db=db,
        )
        assert (
            res_fallo_sync.get("respuesta_usuario")
            == "No pude consultar tu saldo en este momento. Probá de nuevo en unos minutos."
        )

    # 2. Fallo en calcular_frescura
    with patch(
        "app.services.frescura_service.calcular_frescura",
        side_effect=RuntimeError("Error simulado en cálculo de frescura"),
    ):
        res_fallo_frescura: dict = {}
        enriquecer_respuesta_por_intent(
            intent_detectado="consultar_saldo",
            resultado_ia=res_fallo_frescura,
            mensaje_texto="saldo",
            usuario=usuario_test,
            db=db,
        )
        texto = res_fallo_frescura.get("respuesta_usuario", "")
        # Debe contener la información estándar de saldo
        assert "Tus saldos en pesos:" in texto
        # No debe contener la línea de aviso de frescura
        assert "Ojo: hace" not in texto

