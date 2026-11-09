# -*- coding: utf-8 -*-
"""
tests/test_reporte_uso_consejos.py — Pruebas de la métrica de consejos en el reporte semanal de uso.
"""
from __future__ import annotations

from datetime import date, datetime
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
from app.models.consejo import ConsejoEmitido
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.reporte_uso_service import armar_texto, calcular_reporte
from app.utils.fecha import TZ_ARGENTINA


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
        Base.metadata.drop_all(bind=engine)


def test_reporte_uso_consejos_semana(db: Session):
    # Dos usuarios
    u1 = Usuario(
        id=uuid4(),
        email="u1_reporte@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    u2 = Usuario(
        id=uuid4(),
        email="u2_reporte@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add_all([u1, u2])
    db.commit()

    # 14 Consejos distribuidos entre u1 y u2 con claves distintas
    # (1) emitido 26/10, respuesta "sirvio", fecha_respuesta 27/10 15:00 AR
    c1 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u1.id,
        tipo="c1_hormiga",
        clave="k1",
        texto="Consejo 1",
        valores={},
        fecha_emision=date(2026, 10, 26),
        fecha_vence=date(2026, 11, 2),
        respuesta="sirvio",
        fecha_respuesta=datetime(2026, 10, 27, 15, 0, tzinfo=TZ_ARGENTINA),
    )

    # (2) emitido 01/11 sin respuesta
    c2 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u2.id,
        tipo="c1_hormiga",
        clave="k2",
        texto="Consejo 2",
        valores={},
        fecha_emision=date(2026, 11, 1),
        fecha_vence=date(2026, 11, 8),
        respuesta=None,
        fecha_respuesta=None,
    )

    # (3) emitido 25/10, "no_sirve" el 26/10 10:00 AR
    c3 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u1.id,
        tipo="c2_plata_quieta",
        clave="k3",
        texto="Consejo 3",
        valores={},
        fecha_emision=date(2026, 10, 25),
        fecha_vence=date(2026, 11, 1),
        respuesta="no_sirve",
        fecha_respuesta=datetime(2026, 10, 26, 10, 0, tzinfo=TZ_ARGENTINA),
    )

    # (4) emitido 20/10, "no_avisar" el 02/11 10:00 AR
    c4 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u2.id,
        tipo="c3_colchon",
        clave="k4",
        texto="Consejo 4",
        valores={},
        fecha_emision=date(2026, 10, 20),
        fecha_vence=date(2026, 10, 27),
        respuesta="no_avisar",
        fecha_respuesta=datetime(2026, 11, 2, 10, 0, tzinfo=TZ_ARGENTINA),
    )

    # (5) emitido 20/10, "no_avisar" el 01/11 23:30 AR
    c5 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u1.id,
        tipo="c4_comisiones",
        clave="k5",
        texto="Consejo 5",
        valores={},
        fecha_emision=date(2026, 10, 20),
        fecha_vence=date(2026, 10, 27),
        respuesta="no_avisar",
        fecha_respuesta=datetime(2026, 11, 1, 23, 30, tzinfo=TZ_ARGENTINA),
    )

    # (6) emitido 20/10, "resuelto" el 30/10 12:00 AR
    c6 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u2.id,
        tipo="c5_pico",
        clave="k6",
        texto="Consejo 6",
        valores={},
        fecha_emision=date(2026, 10, 20),
        fecha_vence=date(2026, 10, 27),
        respuesta="resuelto",
        fecha_respuesta=datetime(2026, 10, 30, 12, 0, tzinfo=TZ_ARGENTINA),
    )

    # (7) emitido 22/10, vence 29/10, "vencido"
    c7 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u1.id,
        tipo="c6_sin_rubro",
        clave="k7",
        texto="Consejo 7",
        valores={},
        fecha_emision=date(2026, 10, 22),
        fecha_vence=date(2026, 10, 29),
        respuesta="vencido",
        fecha_respuesta=None,
    )

    # (8) emitido 19/10, vence 26/10, "vencido"
    c8 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u2.id,
        tipo="c7_sin_ingresos",
        clave="k8",
        texto="Consejo 8",
        valores={},
        fecha_emision=date(2026, 10, 19),
        fecha_vence=date(2026, 10, 26),
        respuesta="vencido",
        fecha_respuesta=None,
    )

    # (9) emitido 18/10, vence 25/10, "vencido"
    c9 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u1.id,
        tipo="c1_hormiga",
        clave="k9",
        texto="Consejo 9",
        valores={},
        fecha_emision=date(2026, 10, 18),
        fecha_vence=date(2026, 10, 25),
        respuesta="vencido",
        fecha_respuesta=None,
    )

    # (10) emitido 02/11 sin respuesta
    c10 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u2.id,
        tipo="c1_hormiga",
        clave="k10",
        texto="Consejo 10",
        valores={},
        fecha_emision=date(2026, 11, 2),
        fecha_vence=date(2026, 11, 9),
        respuesta=None,
        fecha_respuesta=None,
    )

    # (11) a (14) emitidos 01/09, vencen 08/09, "vencido"
    # mediciones: {"resultado": "mejoro"} 28/10, {"resultado": "igual"} 01/11, {"resultado": "sin_dato"} 30/10, {"resultado": "mejoro"} 02/11
    c11 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u1.id,
        tipo="c1_hormiga",
        clave="k11",
        texto="Consejo 11",
        valores={},
        fecha_emision=date(2026, 9, 1),
        fecha_vence=date(2026, 9, 8),
        respuesta="vencido",
        medicion={"resultado": "mejoro"},
        fecha_medicion=date(2026, 10, 28),
    )
    c12 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u2.id,
        tipo="c1_hormiga",
        clave="k12",
        texto="Consejo 12",
        valores={},
        fecha_emision=date(2026, 9, 1),
        fecha_vence=date(2026, 9, 8),
        respuesta="vencido",
        medicion={"resultado": "igual"},
        fecha_medicion=date(2026, 11, 1),
    )
    c13 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u1.id,
        tipo="c1_hormiga",
        clave="k13",
        texto="Consejo 13",
        valores={},
        fecha_emision=date(2026, 9, 1),
        fecha_vence=date(2026, 9, 8),
        respuesta="vencido",
        medicion={"resultado": "sin_dato"},
        fecha_medicion=date(2026, 10, 30),
    )
    c14 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u2.id,
        tipo="c1_hormiga",
        clave="k14",
        texto="Consejo 14",
        valores={},
        fecha_emision=date(2026, 9, 1),
        fecha_vence=date(2026, 9, 8),
        respuesta="vencido",
        medicion={"resultado": "mejoro"},
        fecha_medicion=date(2026, 11, 2),
    )

    db.add_all([c1, c2, c3, c4, c5, c6, c7, c8, c9, c10, c11, c12, c13, c14])
    db.commit()

    # Hoy = 02/11/2026 (lunes). La semana cerrada es del lunes 26/10 al domingo 01/11/2026
    rep = calcular_reporte(db, hoy=date(2026, 11, 2))

    assert rep["consejos"] == {
        "emitidos": 2,
        "sirvio": 1,
        "no_sirve": 1,
        "no_avisar": 1,
        "resueltos": 1,
        "vencidos": 2,
        "medidos": 2,
        "mejoro": 1,
    }

    texto = armar_texto(rep, hoy=date(2026, 11, 2))
    linea_esperada = (
        "Consejos del inicio: 2 mostrados. Respuestas: me sirvió 1, no me sirve 1, no avisar más 1, "
        "hicieron lo que proponía 1, vencieron sin respuesta 2. Medidos: 2 (mejoró en 1)."
    )
    assert linea_esperada in texto

    # Entre Funciones y WhatsApp en "Otros"
    idx_funciones = texto.index("Funciones de la web")
    idx_consejos = texto.index("Consejos del inicio:")
    idx_whatsapp = texto.index("WhatsApp en \"Otros\"")
    assert idx_funciones < idx_consejos < idx_whatsapp

    # Ningún dato personal en el texto
    assert "u1_reporte@argentum.com" not in texto
    assert "u2_reporte@argentum.com" not in texto
    assert str(u1.id) not in texto
    assert str(u2.id) not in texto
