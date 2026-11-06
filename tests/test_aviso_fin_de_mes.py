# -*- coding: utf-8 -*-
"""
tests/test_aviso_fin_de_mes.py — Pruebas del servicio de aviso de fin de mes (fase_i3_avisos).
Funciones puras, integración con base de datos, paridad sin parche y ejecución en el job diario.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.core.jobs_catalogo import JOBS_CATALOGO
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.consejo import ConsejoEmitido
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.aviso_fin_de_mes_service import (
    avisar_fin_de_mes,
    avisos_que_corresponden,
    faltante_aviso,
    grupo_aviso,
    monedas_aviso,
    monedas_con_saldo,
    texto_aviso_fin_de_mes,
)
from app.services.fin_de_mes_service import calcular_fin_de_mes
from app.services.frescura_service import Frescura
from app.services.inflacion_esperada_service import InflacionEsperada
from app.utils.fecha import TZ_ARGENTINA


@dataclass
class DummyResultadoFinDeMes:
    inicio_ciclo: date
    fin_ciclo: date
    libre_ars: Decimal
    libre_usd: Decimal
    datos_viejos: bool = False
    compromisos_ars: list = field(default_factory=lambda: [{"tipo": "suscripcion", "monto": Decimal("1000"), "fecha": date(2026, 11, 20)}])
    compromisos_usd: list = field(default_factory=lambda: [{"tipo": "suscripcion", "monto": Decimal("100"), "fecha": date(2026, 11, 20)}])


# ==============================================================================
# FIXTURES
# ==============================================================================

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


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario_test(
    db: Session,
    email: str,
    moneda_principal: Moneda | None = Moneda.ARS,
    moneda_secundaria_activa: bool = False,
    estado: EstadoUsuario = EstadoUsuario.ACTIVO,
    onboarding_completo: bool = True,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=estado,
        moneda_principal=moneda_principal,
        moneda_secundaria_activa=moneda_secundaria_activa,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=onboarding_completo,
    )
    db.add(u)
    db.flush()
    return u


# ==============================================================================
# 1. FUNCIONES PURAS
# ==============================================================================

def test_faltante_aviso_puras():
    assert faltante_aviso(Decimal("-40000"), "ARS") == Decimal("40000")
    assert faltante_aviso(Decimal("-1"), "ARS") == Decimal("1")
    assert faltante_aviso(Decimal("-0.5"), "ARS") == Decimal("1")
    assert faltante_aviso(Decimal("-0.49"), "ARS") is None
    assert faltante_aviso(Decimal("0"), "ARS") is None
    assert faltante_aviso(Decimal("10"), "ARS") is None


def test_texto_aviso_fin_de_mes_puras():
    fin = date(2026, 11, 30)

    t1 = texto_aviso_fin_de_mes(Decimal("40000"), "ARS", fin)
    assert t1 == "Ojo: con lo que tenés hoy te faltan $40.000 para cubrir lo que vence hasta el 30/11."
    assert len(t1) <= 90 and "\n" not in t1

    t2 = texto_aviso_fin_de_mes(Decimal("123456789"), "ARS", fin)
    assert t2 == "Ojo: con lo que tenés hoy te faltan $123.456.789 para cubrir lo que vence hasta el 30/11."
    assert len(t2) == 89
    assert len(t2) <= 90 and "\n" not in t2

    t3 = texto_aviso_fin_de_mes(Decimal("1234567890"), "ARS", fin)
    assert t3 == "Ojo: te faltan $1.234.567.890 para cubrir lo que vence hasta el 30/11."
    assert len(t3) <= 90 and "\n" not in t3

    t4 = texto_aviso_fin_de_mes(Decimal("500"), "USD", fin)
    assert t4 == "Ojo: en dólares te faltan US$500 para cubrir lo que vence hasta el 30/11."
    assert len(t4) <= 90 and "\n" not in t4


def test_monedas_aviso_puras():
    u_p = Usuario(moneda_principal=Moneda.ARS, moneda_secundaria_activa=False)
    assert monedas_aviso(u_p) == ("ARS",)

    u_d = Usuario(moneda_principal=Moneda.USD, moneda_secundaria_activa=False)
    assert monedas_aviso(u_d) == ("ARS", "USD")

    u_m = Usuario(moneda_principal=Moneda.ARS, moneda_secundaria_activa=True)
    assert monedas_aviso(u_m) == ("ARS", "USD")

    u_none = Usuario(moneda_principal=None, moneda_secundaria_activa=False)
    assert monedas_aviso(u_none) == ("ARS",)


def test_avisos_que_corresponden_puras():
    fin = date(2026, 11, 30)
    un_comp = {"tipo": "suscripcion", "monto": Decimal("1000"), "fecha": date(2026, 11, 20)}
    r_prueba = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=fin,
        libre_ars=Decimal("-40000"),
        libre_usd=Decimal("-500"),
        datos_viejos=False,
        compromisos_ars=[un_comp],
        compromisos_usd=[un_comp],
    )

    # con_saldo {"ARS", "USD"} y ("ARS", "USD") -> 2 (igual que antes)
    a2 = avisos_que_corresponden(r_prueba, ("ARS", "USD"), con_saldo=frozenset({"ARS", "USD"}))
    assert len(a2) == 2
    assert a2[0][0] == "ARS"
    assert a2[1][0] == "USD"
    assert a2[1][1] == Decimal("500")
    assert a2[1][2] == "Ojo: en dólares te faltan US$500 para cubrir lo que vence hasta el 30/11."

    # con_saldo {"ARS"} -> solo ARS
    a1 = avisos_que_corresponden(r_prueba, ("ARS", "USD"), con_saldo=frozenset({"ARS"}))
    assert len(a1) == 1
    assert a1[0][0] == "ARS"
    assert a1[0][1] == Decimal("40000")
    assert a1[0][2] == "Ojo: con lo que tenés hoy te faltan $40.000 para cubrir lo que vence hasta el 30/11."

    # con_saldo vacío -> []
    assert avisos_que_corresponden(r_prueba, ("ARS", "USD"), con_saldo=frozenset()) == []

    # compromisos_ars vacío con con_saldo {"ARS"} -> []
    r_sin_comp = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=fin,
        libre_ars=Decimal("-40000"),
        libre_usd=Decimal("-500"),
        datos_viejos=False,
        compromisos_ars=[],
        compromisos_usd=[un_comp],
    )
    assert avisos_que_corresponden(r_sin_comp, ("ARS", "USD"), con_saldo=frozenset({"ARS"})) == []

    # Con datos viejos -> []
    r_viejos = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=fin,
        libre_ars=Decimal("-40000"),
        libre_usd=Decimal("-500"),
        datos_viejos=True,
        compromisos_ars=[un_comp],
        compromisos_usd=[un_comp],
    )
    assert avisos_que_corresponden(r_viejos, ("ARS", "USD"), con_saldo=frozenset({"ARS", "USD"})) == []

    # Con libre_ars 0 y ("ARS",) con con_saldo {"ARS"} -> []
    r_cero = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=fin,
        libre_ars=Decimal("0"),
        libre_usd=Decimal("-500"),
        datos_viejos=False,
        compromisos_ars=[un_comp],
        compromisos_usd=[un_comp],
    )
    assert avisos_que_corresponden(r_cero, ("ARS",), con_saldo=frozenset({"ARS"})) == []


def test_grupo_aviso_puras():
    assert grupo_aviso("ARS", date(2026, 11, 1)) == "PROYECCION_NEGATIVA_ARS_20261101"


# ==============================================================================
# 2. BASE CON calcular_fin_de_mes PARCHEADO
# ==============================================================================

def test_monedas_con_saldo(db: Session):
    u = _crear_usuario_test(db, "saldo_test@test.com", Moneda.ARS, False)
    u_otro = _crear_usuario_test(db, "otro_saldo@test.com", Moneda.ARS, False)
    db.commit()

    commit_count = 0
    flush_count = 0
    orig_commit = db.commit
    orig_flush = db.flush

    def tracked_commit(*args, **kwargs):
        nonlocal commit_count
        commit_count += 1
        return orig_commit(*args, **kwargs)

    def tracked_flush(*args, **kwargs):
        nonlocal flush_count
        flush_count += 1
        return orig_flush(*args, **kwargs)

    db.commit = tracked_commit
    db.flush = tracked_flush

    # Sin billeteras -> vacío
    assert monedas_con_saldo(db, u.id) == frozenset()

    # billetera ARS activa con tiene_punto_partida True → {"ARS"}
    b_ars = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta ARS",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        tiene_punto_partida=True,
        fecha_ultimo_control=None,
    )
    db.add(b_ars)
    orig_flush()
    assert monedas_con_saldo(db, u.id) == frozenset({"ARS"})

    # con tiene_punto_partida False y fecha_ultimo_control nula → vacío
    b_ars.tiene_punto_partida = False
    b_ars.fecha_ultimo_control = None
    orig_flush()
    assert monedas_con_saldo(db, u.id) == frozenset()

    # con tiene_punto_partida False y fecha_ultimo_control 01/11/2026 → {"ARS"}
    b_ars.fecha_ultimo_control = date(2026, 11, 1)
    orig_flush()
    assert monedas_con_saldo(db, u.id) == frozenset({"ARS"})

    # una de inversión con punto de partida → no cuenta
    b_inv = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="FCI ARS",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=True,
        tiene_punto_partida=True,
        fecha_ultimo_control=None,
    )
    db.add(b_inv)
    b_ars.tiene_punto_partida = False
    b_ars.fecha_ultimo_control = None
    orig_flush()
    assert monedas_con_saldo(db, u.id) == frozenset()

    # una ARCHIVADA (o no ACTIVA) con punto de partida → no cuenta
    b_arch = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Vieja ARS",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ARCHIVADA,
        es_inversion=False,
        tiene_punto_partida=True,
        fecha_ultimo_control=None,
    )
    db.add(b_arch)
    orig_flush()
    assert monedas_con_saldo(db, u.id) == frozenset()

    # USD con punto de partida → {"USD"}
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Dólares",
        moneda=Moneda.USD,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        tiene_punto_partida=True,
        fecha_ultimo_control=None,
    )
    db.add(b_usd)
    orig_flush()
    assert monedas_con_saldo(db, u.id) == frozenset({"USD"})

    # otro usuario con billeteras no cambia el resultado
    b_otro = Billetera(
        id=uuid4(),
        usuario_id=u_otro.id,
        nombre="Otro ARS",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        tiene_punto_partida=True,
        fecha_ultimo_control=None,
    )
    db.add(b_otro)
    orig_flush()
    assert monedas_con_saldo(db, u.id) == frozenset({"USD"})
    assert monedas_con_saldo(db, u_otro.id) == frozenset({"ARS"})

    # Restaurar y verificar commit y flush contados = 0
    db.commit = orig_commit
    db.flush = orig_flush
    assert commit_count == 0
    assert flush_count == 0


def test_avisar_fin_de_mes_base_parcheada(db: Session, monkeypatch):
    u_p = _crear_usuario_test(db, "p@test.com", Moneda.ARS, False)
    u_d = _crear_usuario_test(db, "d@test.com", Moneda.USD, False)
    u_m = _crear_usuario_test(db, "m@test.com", Moneda.ARS, True)
    u_otro = _crear_usuario_test(db, "otro@test.com", Moneda.ARS, False)

    def _crear_billetera_activa(user_id, moneda):
        b = Billetera(
            id=uuid4(),
            usuario_id=user_id,
            nombre=f"Billetera {moneda}",
            moneda=Moneda[moneda] if hasattr(Moneda, moneda) else moneda,
            estado=EstadoBilletera.ACTIVA,
            es_inversion=False,
            tiene_punto_partida=True,
            fecha_ultimo_control=None,
        )
        db.add(b)
        return b

    _crear_billetera_activa(u_p.id, "ARS")
    _crear_billetera_activa(u_d.id, "USD")
    _crear_billetera_activa(u_m.id, "ARS")
    _crear_billetera_activa(u_m.id, "USD")
    _crear_billetera_activa(u_otro.id, "ARS")
    db.commit()

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-40000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )

    monkeypatch.setattr(
        "app.services.aviso_fin_de_mes_service.calcular_fin_de_mes",
        lambda db_session, user, hoy=None: resultado_mock,
    )

    # P con libre_ars -40.000 -> avisar_fin_de_mes da 1
    hoy = date(2026, 11, 5)
    cant = avisar_fin_de_mes(db, u_p, hoy=hoy)
    assert cant == 1

    notif = db.query(Notificacion).filter(
        Notificacion.usuario_id == u_p.id,
        Notificacion.tipo == TipoNotificacion.PROYECCION_NEGATIVA,
    ).first()
    assert notif is not None
    assert notif.nivel == NivelNotificacion.FINANCIERA_IMPORTANTE
    assert notif.mensaje == "Ojo: con lo que tenés hoy te faltan $40.000 para cubrir lo que vence hasta el 30/11."
    assert notif.canal_web is True
    assert notif.canal_whatsapp is True
    assert notif.deep_link == "/app/dashboard"
    assert notif.grupo_agrupacion == "PROYECCION_NEGATIVA_ARS_20261101"
    assert notif.datos_template == {
        "moneda": "ARS",
        "inicio_ciclo": "2026-11-01",
        "fin_ciclo": "2026-11-30",
        "faltante": "40000",
    }

    # Otra vez el mismo día -> 0
    assert avisar_fin_de_mes(db, u_p, hoy=hoy) == 0

    # Al otro día (06/11) con -60.000 -> 0 (una vez por ciclo)
    resultado_mock.libre_ars = Decimal("-60000")
    assert avisar_fin_de_mes(db, u_p, hoy=date(2026, 11, 6)) == 0

    # Con inicio_ciclo 01/12/2026 y fin 31/12/2026 -> 1 (ciclo nuevo)
    resultado_mock.inicio_ciclo = date(2026, 12, 1)
    resultado_mock.fin_ciclo = date(2026, 12, 31)
    resultado_mock.libre_ars = Decimal("-40000")
    assert avisar_fin_de_mes(db, u_p, hoy=date(2026, 12, 5)) == 1

    # datos_viejos True -> 0
    resultado_mock.datos_viejos = True
    assert avisar_fin_de_mes(db, u_p, hoy=date(2026, 12, 6)) == 0
    resultado_mock.datos_viejos = False

    # libre_ars -1 -> 1 con "$1" (en un ciclo nuevo 01/01/2027)
    resultado_mock.inicio_ciclo = date(2027, 1, 1)
    resultado_mock.fin_ciclo = date(2027, 1, 31)
    resultado_mock.libre_ars = Decimal("-1")
    assert avisar_fin_de_mes(db, u_p, hoy=date(2027, 1, 5)) == 1
    notif_1 = db.query(Notificacion).filter(
        Notificacion.usuario_id == u_p.id,
        Notificacion.grupo_agrupacion == "PROYECCION_NEGATIVA_ARS_20270101",
    ).first()
    assert notif_1 is not None
    assert "$1 para cubrir" in notif_1.mensaje

    # libre_ars 0 -> 0
    resultado_mock.inicio_ciclo = date(2027, 2, 1)
    resultado_mock.fin_ciclo = date(2027, 2, 28)
    resultado_mock.libre_ars = Decimal("0")
    assert avisar_fin_de_mes(db, u_p, hoy=date(2027, 2, 5)) == 0

    # Config con proyeccion_negativa_activo False -> 0
    cfg = db.query(ConfiguracionNotificacion).filter(ConfiguracionNotificacion.usuario_id == u_p.id).first()
    if not cfg:
        cfg = ConfiguracionNotificacion(usuario_id=u_p.id)
        db.add(cfg)
    cfg.proyeccion_negativa_activo = False
    db.commit()
    resultado_mock.libre_ars = Decimal("-40000")
    assert avisar_fin_de_mes(db, u_p, hoy=date(2027, 2, 6)) == 0

    # web False y whatsapp False -> 0
    cfg.proyeccion_negativa_activo = True
    cfg.proyeccion_negativa_web = False
    cfg.proyeccion_negativa_whatsapp = False
    db.commit()
    assert avisar_fin_de_mes(db, u_p, hoy=date(2027, 2, 6)) == 0

    # web True y whatsapp False -> 1 con canal_whatsapp False
    cfg.proyeccion_negativa_web = True
    cfg.proyeccion_negativa_whatsapp = False
    db.commit()
    assert avisar_fin_de_mes(db, u_p, hoy=date(2027, 2, 6)) == 1
    notif_web = db.query(Notificacion).filter(
        Notificacion.usuario_id == u_p.id,
        Notificacion.grupo_agrupacion == "PROYECCION_NEGATIVA_ARS_20270201",
    ).first()
    assert notif_web is not None
    assert notif_web.canal_web is True
    assert notif_web.canal_whatsapp is False

    # D con libre_ars 0 y libre_usd -500 -> 1, mensaje "Ojo: en dólares te faltan US$500 para cubrir lo que vence hasta el 30/11.", grupo "PROYECCION_NEGATIVA_USD_20261101"
    resultado_mock.inicio_ciclo = date(2026, 11, 1)
    resultado_mock.fin_ciclo = date(2026, 11, 30)
    resultado_mock.libre_ars = Decimal("0")
    resultado_mock.libre_usd = Decimal("-500")
    assert avisar_fin_de_mes(db, u_d, hoy=hoy) == 1
    notif_d = db.query(Notificacion).filter(
        Notificacion.usuario_id == u_d.id,
        Notificacion.grupo_agrupacion == "PROYECCION_NEGATIVA_USD_20261101",
    ).first()
    assert notif_d is not None
    assert notif_d.mensaje == "Ojo: en dólares te faltan US$500 para cubrir lo que vence hasta el 30/11."

    # D (principal USD) sin billetera USD con saldo cargado -> 0
    u_d_sin = _crear_usuario_test(db, "d_sin@test.com", Moneda.USD, False)
    db.commit()
    assert avisar_fin_de_mes(db, u_d_sin, hoy=hoy) == 0

    # M con los dos negativos -> 2
    resultado_mock.libre_ars = Decimal("-40000")
    resultado_mock.libre_usd = Decimal("-500")
    assert avisar_fin_de_mes(db, u_m, hoy=hoy) == 2

    # P con libre_ars 0 y libre_usd -500 -> 0 (P no tiene USD activa)
    resultado_mock.inicio_ciclo = date(2027, 3, 1)
    resultado_mock.fin_ciclo = date(2027, 3, 31)
    resultado_mock.libre_ars = Decimal("0")
    resultado_mock.libre_usd = Decimal("-500")
    assert avisar_fin_de_mes(db, u_p, hoy=date(2027, 3, 5)) == 0

    # Otro usuario no tiene notificaciones
    assert db.query(Notificacion).filter(Notificacion.usuario_id == u_otro.id).count() == 0

    # Después de todo, ConsejoEmitido del usuario = 0 (no cuenta para el tope)
    assert db.query(ConsejoEmitido).filter(ConsejoEmitido.usuario_id == u_p.id).count() == 0


# ==============================================================================
# 3. PARIDAD SIN PARCHE
# ==============================================================================

def test_paridad_sin_parche(db: Session, monkeypatch):
    hoy = date(2026, 11, 5)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy)

    u = _crear_usuario_test(db, "paridad@test.com", Moneda.ARS, False)

    # Billetera ARS de 100.000 con control de saldo reciente para frescura
    cuenta = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta ARS",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_actual=Decimal("100000"),
        saldo_inicial=Decimal("100000"),
        fecha_ultimo_control=hoy,
    )
    db.add(cuenta)
    db.flush()

    # Transacción de saldo reciente para que calcular_frescura no dé datos viejos
    tx = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=cuenta.id,
        tipo=TipoTransaccion.INGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("100000"),
        fecha=hoy,
        fecha_creacion=datetime(2026, 11, 5, 12, 0, tzinfo=TZ_ARGENTINA),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        saldo_aplicado=True,
    )
    db.add(tx)
    db.flush()

    # Compromiso en pesos de 140.000 que vence el 20/11/2026
    susc = Suscripcion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=cuenta.id,
        nombre="Servicio mensual",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 11, 20),
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(susc)
    db.flush()

    hist = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=susc.id,
        monto=Decimal("140000"),
        moneda="ARS",
        vigente_desde=date(2026, 1, 1),
    )
    db.add(hist)
    db.commit()

    # Parchear frescura al día
    monkeypatch.setattr(
        "app.services.fin_de_mes_service.calcular_frescura",
        lambda *args, **kwargs: Frescura(
            ultima_carga=date(2026, 11, 5),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    r = calcular_fin_de_mes(db, u, hoy=hoy)
    assert r.libre_ars == Decimal("-40000")

    cant = avisar_fin_de_mes(db, u, hoy=hoy)
    assert cant == 1

    notif = db.query(Notificacion).filter(
        Notificacion.usuario_id == u.id,
        Notificacion.tipo == TipoNotificacion.PROYECCION_NEGATIVA,
    ).first()
    assert notif is not None
    assert notif.mensaje == "Ojo: con lo que tenés hoy te faltan $40.000 para cubrir lo que vence hasta el 30/11."

    # Misma P con datos viejos (frescura parcheada con datos_viejos True) -> 0
    monkeypatch.setattr(
        "app.services.fin_de_mes_service.calcular_frescura",
        lambda *args, **kwargs: Frescura(
            ultima_carga=date(2026, 10, 1),
            dias_sin_cargar=35,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=True,
        ),
    )
    # Ciclo siguiente para probar si crearía con datos viejos
    hoy_sig = date(2026, 12, 5)
    assert avisar_fin_de_mes(db, u, hoy=hoy_sig) == 0

    # La misma con la billetera sin punto de partida y sin control (saldo inicial 0 y el saldo que dé) -> 0
    u_sin_saldo = _crear_usuario_test(db, "sin_saldo@test.com", Moneda.ARS, False)
    cuenta_sin = Billetera(
        id=uuid4(),
        usuario_id=u_sin_saldo.id,
        nombre="Cuenta Sin Saldo",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_actual=Decimal("100000"),
        saldo_inicial=Decimal("0"),
        tiene_punto_partida=False,
        fecha_ultimo_control=None,
    )
    db.add(cuenta_sin)
    susc_sin = Suscripcion(
        id=uuid4(),
        usuario_id=u_sin_saldo.id,
        billetera_id=cuenta_sin.id,
        nombre="Servicio mensual",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 11, 20),
        estado=EstadoSuscripcion.ACTIVA,
    )
    db.add(susc_sin)
    db.flush()
    hist_sin = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=susc_sin.id,
        monto=Decimal("140000"),
        moneda="ARS",
        vigente_desde=date(2026, 1, 1),
    )
    db.add(hist_sin)
    db.commit()
    monkeypatch.setattr(
        "app.services.fin_de_mes_service.calcular_frescura",
        lambda *args, **kwargs: Frescura(
            ultima_carga=date(2026, 11, 5),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )
    assert avisar_fin_de_mes(db, u_sin_saldo, hoy=hoy) == 0


def test_caso_usuario_06_sin_parche(db: Session, monkeypatch):
    """
    Caso Usuario_06 (sin parche): usuario P2 con una billetera ARS sin punto de partida,
    saldo inicial 0, y un único egreso de 17.500 el 04/11/2026 (Otros > Regalos),
    sin compromisos, hoy 05/11/2026 y frescura al día -> calcular_fin_de_mes(...).libre_ars == -17.500
    y avisar_fin_de_mes -> 0, sin notificación.
    """
    hoy = date(2026, 11, 5)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy)

    u6 = _crear_usuario_test(db, "usuario_06@test.com", Moneda.ARS, False)

    b6 = Billetera(
        id=uuid4(),
        usuario_id=u6.id,
        nombre="Efectivo",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("-17500"),
        tiene_punto_partida=False,
        fecha_ultimo_control=None,
    )
    db.add(b6)
    db.flush()

    cat = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    db.add(cat)
    db.flush()
    subcat = Subcategoria(id=uuid4(), categoria_id=cat.id, nombre="Regalos")
    db.add(subcat)
    db.flush()

    tx = Transaccion(
        id=uuid4(),
        usuario_id=u6.id,
        billetera_id=b6.id,
        categoria_id=cat.id,
        subcategoria_id=subcat.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("17500"),
        fecha=date(2026, 11, 4),
        fecha_creacion=datetime(2026, 11, 4, 15, 0, tzinfo=TZ_ARGENTINA),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.IA_WPP,
        saldo_aplicado=True,
    )
    db.add(tx)
    db.commit()

    monkeypatch.setattr(
        "app.services.fin_de_mes_service.calcular_frescura",
        lambda *args, **kwargs: Frescura(
            ultima_carga=date(2026, 11, 4),
            dias_sin_cargar=1,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )

    r = calcular_fin_de_mes(db, u6, hoy=hoy)
    assert r.libre_ars == Decimal("-17500")

    cant = avisar_fin_de_mes(db, u6, hoy=hoy)
    assert cant == 0

    notif_count = db.query(Notificacion).filter(
        Notificacion.usuario_id == u6.id,
        Notificacion.tipo == TipoNotificacion.PROYECCION_NEGATIVA,
    ).count()
    assert notif_count == 0


# ==============================================================================
# 4. JOB CONSEJOS DIARIOS
# ==============================================================================

def test_job_consejos_diarios_aviso_fin_de_mes(db: Session, monkeypatch):
    engine = db.get_bind()
    TestSessionMaker = sessionmaker(bind=engine)

    u_act1 = _crear_usuario_test(db, "act1@test.com", estado=EstadoUsuario.ACTIVO, onboarding_completo=True)
    u_inact = _crear_usuario_test(db, "inact@test.com", estado=EstadoUsuario.INACTIVO, onboarding_completo=True)
    u_no_onb = _crear_usuario_test(db, "noonb@test.com", estado=EstadoUsuario.ACTIVO, onboarding_completo=False)
    u_lanza = _crear_usuario_test(db, "lanza@test.com", estado=EstadoUsuario.ACTIVO, onboarding_completo=True)
    u_lanza_emitir = _crear_usuario_test(db, "lanza_emitir@test.com", estado=EstadoUsuario.ACTIVO, onboarding_completo=True)
    u_act2 = _crear_usuario_test(db, "act2@test.com", estado=EstadoUsuario.ACTIVO, onboarding_completo=True)
    db.commit()

    monkeypatch.setattr("app.main.SessionLocal", TestSessionMaker)
    monkeypatch.setattr("app.main.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.main.liberar_lock_job", lambda *a, **kw: None)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 11, 5))
    monkeypatch.setattr(
        "app.services.inflacion_esperada_service.inflacion_esperada",
        lambda *a, **kw: InflacionEsperada(tasas=[Decimal("2.0")], fuente_texto="REM", es_respaldo=False),
    )

    usuarios_emitir = []
    usuarios_avisar = []

    def mock_emitir(session, user, hoy, inflacion):
        usuarios_emitir.append(user.id)
        if user.id == u_lanza_emitir.id:
            raise RuntimeError("Fallo simulado en emitir_si_corresponde")
        return None

    def mock_medir(session, user, hoy):
        return 0

    def mock_avisar(session, user, hoy):
        if user.id == u_lanza.id:
            raise RuntimeError("Fallo simulado en aviso de fin de mes")
        usuarios_avisar.append(user.id)
        return 1

    monkeypatch.setattr("app.services.consejos_service.emitir_si_corresponde", mock_emitir)
    monkeypatch.setattr("app.services.consejos_service.medir_pendientes", mock_medir)
    monkeypatch.setattr("app.services.aviso_fin_de_mes_service.avisar_fin_de_mes", mock_avisar)

    from app.main import _job_consejos_diarios
    _job_consejos_diarios()

    # Se llama para los ACTIVOS con onboarding completo y no para el INACTIVO ni el de onboarding incompleto
    assert u_act1.id in usuarios_avisar
    assert u_act2.id in usuarios_avisar
    assert u_inact.id not in usuarios_avisar
    assert u_no_onb.id not in usuarios_avisar

    # Si avisar_fin_de_mes lanza para un usuario, emitir_si_corresponde de ese y de los siguientes se llamó igual y avisar del siguiente también
    assert u_lanza.id in usuarios_emitir
    assert u_act2.id in usuarios_emitir
    assert u_act2.id in usuarios_avisar

    # Si emitir_si_corresponde lanza para un usuario, avisar_fin_de_mes igual se llama para ese usuario (D-2)
    assert u_lanza_emitir.id in usuarios_emitir
    assert u_lanza_emitir.id in usuarios_avisar

    # Catálogo: que_hace de consejos_diarios es el texto de E-C y len(JOBS_CATALOGO) sigue en 24
    item = next(j for j in JOBS_CATALOGO if j.job_id == "consejos_diarios")
    assert item.que_hace == (
        "Elige el consejo del día de cada persona (tarjeta «Un dato para vos» del inicio), "
        "marca los vencidos, mide si sirvieron y avisa una vez por ciclo si lo que tiene no alcanza para lo que vence."
    )
    assert len(JOBS_CATALOGO) == 24


def test_aislamiento_estatico_aviso_fin_de_mes_y_scheduler():
    """T-1: Ni app/services/aviso_fin_de_mes_service.py ni app/services/notificacion_scheduler_service.py contienen 'consejos_'."""
    backend_dir = Path(__file__).resolve().parent.parent
    for rel_path in [
        "app/services/aviso_fin_de_mes_service.py",
        "app/services/notificacion_scheduler_service.py",
    ]:
        p = backend_dir / rel_path
        contenido = p.read_text(encoding="utf-8")
        assert "consejos_" not in contenido, f"Violación de aislamiento: {rel_path} contiene 'consejos_'"
