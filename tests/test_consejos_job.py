# -*- coding: utf-8 -*-
"""
tests/test_consejos_job.py
Pruebas del job diario de consejos, vencimiento y medición (fase_i1_consejos).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.core.jobs_catalogo import JOBS_CATALOGO, JOBS_MANUALES_PERMITIDOS
from app.main import _job_consejos_diarios
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.consejo import ConsejoEmitido
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
)
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, CicloTipo, Moneda
from app.services.consejos_service import marcar_vencidos, medir_pendientes
from app.services.inflacion_esperada_service import InflacionEsperada


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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db")
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


# ==============================================================================
# 1. MARCAR VENCIDOS
# ==============================================================================

def test_marcar_vencidos(db: Session):
    """marcar_vencidos con uno emitido 01/11 → hoy 07/11 sigue sin respuesta; 08/11 → "vencido"."""
    u = Usuario(
        id=uuid4(),
        email="vencidos@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
    )
    db.add(u)
    db.commit()

    c = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u.id,
        tipo="hormiga",
        clave="hormiga/test",
        moneda="ARS",
        texto="Texto",
        valores={},
        fecha_emision=date(2026, 11, 1),
        fecha_vence=date(2026, 11, 8),
        respuesta=None,
    )
    db.add(c)
    db.commit()

    # hoy 07/11: fecha_vence <= hoy (08/11 <= 07/11) es False -> sigue sin respuesta
    afectados_7 = marcar_vencidos(db, hoy=date(2026, 11, 7))
    assert afectados_7 == 0
    db.refresh(c)
    assert c.respuesta is None

    # hoy 08/11: 08/11 <= 08/11 es True -> "vencido"
    afectados_8 = marcar_vencidos(db, hoy=date(2026, 11, 8))
    assert afectados_8 == 1
    db.refresh(c)
    assert c.respuesta == "vencido"


# ==============================================================================
# 2. JOB DIARIO
# ==============================================================================

def test_job_consejos_diarios(db: Session, monkeypatch):
    """
    _job_consejos_diarios con SessionLocal, lock, hoy_argentina, inflacion_esperada y emitir/medir parcheados:
    - inflacion_esperada se llama 1 vez;
    - un usuario INACTIVO y uno con onboarding_completo False no se procesan;
    - si uno lanza, los demás se procesan igual.
    """
    engine = db.get_bind()
    TestSessionMaker = sessionmaker(bind=engine)

    # 3 usuarios:
    # u1: ACTIVO, onboarding=True (procesa ok)
    # u2: INACTIVO, onboarding=True (no procesa)
    # u3: ACTIVO, onboarding=False (no procesa)
    # u4: ACTIVO, onboarding=True (lanza excepción)
    # u5: ACTIVO, onboarding=True (procesa ok tras la excepción de u4)
    u1 = Usuario(id=uuid4(), email="u1@test.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, onboarding_completo=True)
    u2 = Usuario(id=uuid4(), email="u2@test.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.INACTIVO, onboarding_completo=True)
    u3 = Usuario(id=uuid4(), email="u3@test.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, onboarding_completo=False)
    u4 = Usuario(id=uuid4(), email="u4@test.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, onboarding_completo=True)
    u5 = Usuario(id=uuid4(), email="u5@test.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, onboarding_completo=True)
    db.add_all([u1, u2, u3, u4, u5])
    db.commit()

    monkeypatch.setattr("app.main.SessionLocal", TestSessionMaker)
    monkeypatch.setattr("app.main.intentar_tomar_lock_job", lambda *a, **kw: True)
    monkeypatch.setattr("app.main.liberar_lock_job", lambda *a, **kw: None)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 11, 2))

    llamadas_inflacion = 0

    def mock_inflacion(*a, **kw):
        nonlocal llamadas_inflacion
        llamadas_inflacion += 1
        return InflacionEsperada(tasas=[Decimal("2.1")], fuente_texto="REM", es_respaldo=False)

    monkeypatch.setattr("app.services.inflacion_esperada_service.inflacion_esperada", mock_inflacion)

    usuarios_procesados: list[uuid.UUID] = []

    def mock_emitir(session, user, hoy, inflacion):
        if user.id == u4.id:
            raise RuntimeError("Error simulado en usuario 4")
        usuarios_procesados.append(user.id)
        return None

    def mock_medir(session, user, hoy):
        return 0

    monkeypatch.setattr("app.services.consejos_service.emitir_si_corresponde", mock_emitir)
    monkeypatch.setattr("app.services.consejos_service.medir_pendientes", mock_medir)

    _job_consejos_diarios()

    assert llamadas_inflacion == 1
    # u2 y u3 no se procesaron; u4 lanzó excepción; u1 y u5 se procesaron exitosamente
    assert u1.id in usuarios_procesados
    assert u5.id in usuarios_procesados
    assert u2.id not in usuarios_procesados
    assert u3.id not in usuarios_procesados


# ==============================================================================
# 3. MEDIR PENDIENTES
# ==============================================================================

def test_medir_pendientes(db: Session):
    """
    medir_pendientes con un hormiga de P emitido el 02/11/2026 (valores total "37500.00", subcategorías Taxi y apps y Cafetería):
    diciembre Taxi 8 × 2.500 y enero 2027 Taxi 6 × 2.500 + Cafetería 3 × 3.000 →
    con hoy 31/01/2027 medicion None;
    con hoy 01/02/2027 {"resultado": "mejoro", "antes": "37500.00", "despues": "22000.00"};
    con diciembre Taxi 16 × 2.500 y enero 14 × 2.500 → "igual".
    """
    p = Usuario(
        id=uuid4(),
        email="p_medir@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(p)
    db.commit()

    cuenta = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("200000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(cuenta)

    # Categorías y subcategorías
    c_transporte = Categoria(id=uuid4(), nombre="Transporte", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    c_gastro = Categoria(id=uuid4(), nombre="Gastronomía", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    db.add_all([c_transporte, c_gastro])
    db.commit()

    s_taxi = Subcategoria(id=uuid4(), categoria_id=c_transporte.id, nombre="Taxi y apps", estado=EstadoSubcategoria.ACTIVA)
    s_cafe = Subcategoria(id=uuid4(), categoria_id=c_gastro.id, nombre="Cafetería", estado=EstadoSubcategoria.ACTIVA)
    db.add_all([s_taxi, s_cafe])
    db.commit()

    # Consejo hormiga emitido el 02/11/2026
    c_hormiga = ConsejoEmitido(
        id=uuid4(),
        usuario_id=p.id,
        tipo="hormiga",
        clave="hormiga/ARS/2026-10-01",
        moneda="ARS",
        texto="Texto hormiga",
        valores={
            "total": "37500.00",
            "subcategorias": [["Transporte", "Taxi y apps"], ["Gastronomía", "Cafetería"]],
        },
        fecha_emision=date(2026, 11, 2),
        fecha_vence=date(2026, 11, 9),
        medicion=None,
    )
    db.add(c_hormiga)
    db.commit()

    # diciembre 2026: Taxi 8 × 2.500 = 20.000
    for _ in range(8):
        db.add(
            Transaccion(
                id=uuid4(),
                usuario_id=p.id,
                billetera_id=cuenta.id,
                categoria_id=c_transporte.id,
                subcategoria_id=s_taxi.id,
                monto=Decimal("2500"),
                moneda=Moneda.ARS,
                tipo=TipoTransaccion.EGRESO,
                fecha=date(2026, 12, 10),
                metodo_pago=MetodoPago.DEBITO,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                origen=OrigenTransaccion.MANUAL,
            )
        )

    # enero 2027: Taxi 6 × 2.500 (15.000) + Cafetería 3 × 3.000 (9.000) = 24.000
    for _ in range(6):
        db.add(
            Transaccion(
                id=uuid4(),
                usuario_id=p.id,
                billetera_id=cuenta.id,
                categoria_id=c_transporte.id,
                subcategoria_id=s_taxi.id,
                monto=Decimal("2500"),
                moneda=Moneda.ARS,
                tipo=TipoTransaccion.EGRESO,
                fecha=date(2027, 1, 10),
                metodo_pago=MetodoPago.DEBITO,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                origen=OrigenTransaccion.MANUAL,
            )
        )
    for _ in range(3):
        db.add(
            Transaccion(
                id=uuid4(),
                usuario_id=p.id,
                billetera_id=cuenta.id,
                categoria_id=c_gastro.id,
                subcategoria_id=s_cafe.id,
                monto=Decimal("3000"),
                moneda=Moneda.ARS,
                tipo=TipoTransaccion.EGRESO,
                fecha=date(2027, 1, 15),
                metodo_pago=MetodoPago.DEBITO,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                origen=OrigenTransaccion.MANUAL,
            )
        )
    db.commit()

    # con hoy 31/01/2027: medicion None
    medir_pendientes(db, p, hoy=date(2027, 1, 31))
    db.refresh(c_hormiga)
    assert c_hormiga.medicion is None

    # con hoy 01/02/2027: {"resultado": "mejoro", "antes": "37500.00", "despues": "22000.00"}
    cant_medidos = medir_pendientes(db, p, hoy=date(2027, 2, 1))
    assert cant_medidos == 1
    db.refresh(c_hormiga)
    assert c_hormiga.medicion == {
        "resultado": "mejoro",
        "antes": "37500.00",
        "despues": "22000.00",
    }
    assert c_hormiga.fecha_medicion == date(2027, 2, 1)

    # Caso "igual": con diciembre Taxi 16 × 2.500 y enero 14 × 2.500 → "igual"
    # Reiniciar medicion del consejo
    c_hormiga.medicion = None
    c_hormiga.fecha_medicion = None
    db.commit()

    # Borrar transacciones anteriores de diciembre y enero
    db.query(Transaccion).filter(Transaccion.usuario_id == p.id).delete()
    db.commit()

    # Diciembre Taxi 16 × 2.500 = 40.000
    for _ in range(16):
        db.add(
            Transaccion(
                id=uuid4(),
                usuario_id=p.id,
                billetera_id=cuenta.id,
                categoria_id=c_transporte.id,
                subcategoria_id=s_taxi.id,
                monto=Decimal("2500"),
                moneda=Moneda.ARS,
                tipo=TipoTransaccion.EGRESO,
                fecha=date(2026, 12, 10),
                metodo_pago=MetodoPago.DEBITO,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                origen=OrigenTransaccion.MANUAL,
            )
        )
    # Enero Taxi 14 × 2.500 = 35.000
    for _ in range(14):
        db.add(
            Transaccion(
                id=uuid4(),
                usuario_id=p.id,
                billetera_id=cuenta.id,
                categoria_id=c_transporte.id,
                subcategoria_id=s_taxi.id,
                monto=Decimal("2500"),
                moneda=Moneda.ARS,
                tipo=TipoTransaccion.EGRESO,
                fecha=date(2027, 1, 10),
                metodo_pago=MetodoPago.DEBITO,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                origen=OrigenTransaccion.MANUAL,
            )
        )
    db.commit()

    medir_pendientes(db, p, hoy=date(2027, 2, 1))
    db.refresh(c_hormiga)
    assert c_hormiga.medicion is not None
    assert c_hormiga.medicion["resultado"] == "igual"
    assert c_hormiga.medicion["antes"] == "37500.00"
    assert c_hormiga.medicion["despues"] == "37500.00"


# ==============================================================================
# 4. CATÁLOGO DE JOBS
# ==============================================================================

def test_catalogo_jobs_consejos_diarios():
    """Catálogo: "consejos_diarios" con puede_correr_ahora True y en JOBS_MANUALES_PERMITIDOS."""
    assert "consejos_diarios" in JOBS_MANUALES_PERMITIDOS
    item = next((j for j in JOBS_CATALOGO if j.job_id == "consejos_diarios"), None)
    assert item is not None
    assert item.puede_correr_ahora is True
    assert item.periodo_esperado_minutos == 1440
