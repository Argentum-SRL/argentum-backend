# -*- coding: utf-8 -*-
"""
tests/test_aviso_fin_de_mes_wpp.py — Pruebas del agregado de aviso de fin de mes
al buzón de respuesta de WhatsApp (fase_i3_avisos).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
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
from app.models.billetera import Billetera, EstadoBilletera
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.aviso_fin_de_mes_wpp import agregar_aviso_fin_de_mes
from app.services import whatsapp_service
from app.services.whatsapp_service import buzon_de_respuesta, enviar_whatsapp


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
    email: str = "wpp_user@test.com",
    telefono: str = "+5491112345678",
    moneda_principal: Moneda = Moneda.ARS,
    moneda_secundaria_activa: bool = False,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email,
        telefono=telefono,
        telefono_normalizado=telefono,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=moneda_principal,
        moneda_secundaria_activa=moneda_secundaria_activa,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u)
    db.flush()

    b_ars = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        tiene_punto_partida=True,
        fecha_ultimo_control=None,
    )
    db.add(b_ars)

    if moneda_principal == Moneda.USD or moneda_secundaria_activa:
        b_usd = Billetera(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Billetera USD",
            moneda=Moneda.USD,
            estado=EstadoBilletera.ACTIVA,
            es_inversion=False,
            tiene_punto_partida=True,
            fecha_ultimo_control=None,
        )
        db.add(b_usd)

    db.commit()
    db.refresh(u)
    return u


def _crear_notificacion_proyeccion(
    db: Session,
    usuario_id: uuid.UUID,
    moneda: str = "ARS",
    inicio_ciclo: str = "2026-11-01",
    fin_ciclo: str = "2026-11-30",
    faltante: str = "40000",
    canal_whatsapp: bool = True,
    enviada_whatsapp: bool = False,
    archivada: bool = False,
    datos_template: dict | None = None,
) -> Notificacion:
    if datos_template is None:
        datos_template = {
            "moneda": moneda,
            "inicio_ciclo": inicio_ciclo,
            "fin_ciclo": fin_ciclo,
            "faltante": faltante,
        }

    n = Notificacion(
        id=uuid4(),
        usuario_id=usuario_id,
        tipo=TipoNotificacion.PROYECCION_NEGATIVA,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje="Aviso de proyección negativa",
        canal_web=True,
        canal_whatsapp=canal_whatsapp,
        enviada_whatsapp=enviada_whatsapp,
        archivada=archivada,
        grupo_agrupacion=f"PROYECCION_NEGATIVA_{moneda}_{inicio_ciclo.replace('-', '')}",
        datos_template=datos_template,
        created_at=datetime.now(timezone.utc),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


# ==============================================================================
# PRUEBAS DE AVISO DE FIN DE MES POR WHATSAPP
# ==============================================================================

def test_caso_a_p_con_aviso_pendiente(db: Session, monkeypatch):
    """
    (a) P con una PROYECCION_NEGATIVA pendiente del ciclo y libre de hoy −50.000:
    el único envío es "<respuesta>\nOjo: con lo que tenés hoy te faltan $50.000 para cubrir lo que vence hasta el 30/11."
    y la notificación queda con enviada_whatsapp True. Un segundo mensaje sale sin línea extra.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_a@test.com", telefono="+5491100000001")
    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )

    llamadas_fin_de_mes = 0

    def mock_fin_de_mes(*a, **kw):
        nonlocal llamadas_fin_de_mes
        llamadas_fin_de_mes += 1
        return resultado_mock

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        mock_fin_de_mes,
    )

    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Listo.")
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is True

    assert mock_enviar.call_count == 1
    texto_saliente = mock_enviar.call_args[1].get("texto")
    assert texto_saliente == "Listo.\nOjo: con lo que tenés hoy te faltan $50.000 para cubrir lo que vence hasta el 30/11."

    db.refresh(notif)
    assert notif.enviada_whatsapp is True

    # Un segundo mensaje sale sin línea extra
    mock_enviar.reset_mock()
    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Segundo mensaje.")
        ok2 = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok2 is False

    assert mock_enviar.call_args[1].get("texto") == "Segundo mensaje."


def test_caso_b_buzon_con_aviso_previo(db: Session, monkeypatch):
    """
    (b) Con el buzón que ya tiene un aviso (agregar_aviso de prueba antes):
    False, la notificación sigue pendiente y calcular_fin_de_mes se llamó 0 veces.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_b@test.com", telefono="+5491100000002")
    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    llamadas_fin_de_mes = 0

    def mock_fin_de_mes(*a, **kw):
        nonlocal llamadas_fin_de_mes
        llamadas_fin_de_mes += 1
        return None

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        mock_fin_de_mes,
    )

    with buzon_de_respuesta(u.telefono) as bz:
        bz.agregar_aviso("Aviso previo de prueba", "test")
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    assert llamadas_fin_de_mes == 0
    db.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_c_sin_notificaciones_pendientes(db: Session, monkeypatch):
    """
    (c) Sin notificaciones pendientes: False y 0 llamadas a calcular_fin_de_mes.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_c@test.com", telefono="+5491100000003")

    llamadas_fin_de_mes = 0

    def mock_fin_de_mes(*a, **kw):
        nonlocal llamadas_fin_de_mes
        llamadas_fin_de_mes += 1
        return None

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        mock_fin_de_mes,
    )

    with buzon_de_respuesta(u.telefono):
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    assert llamadas_fin_de_mes == 0


def test_caso_d_datos_viejos(db: Session, monkeypatch):
    """
    (d) datos_viejos True: False y sigue pendiente.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_d@test.com", telefono="+5491100000004")
    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=True,
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    with buzon_de_respuesta(u.telefono):
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    db.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_e_libre_hoy_cero(db: Session, monkeypatch):
    """
    (e) libre de hoy 0: False, sin línea, la notificación queda con enviada_whatsapp True (descartada).
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_e@test.com", telefono="+5491100000005")
    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("0"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    with buzon_de_respuesta(u.telefono):
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    db.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_caso_f_ciclo_anterior_y_sin_datos_template(db: Session, monkeypatch):
    """
    (f) Notificación del ciclo anterior (inicio_ciclo "2026-10-01") y otra vieja sin datos_template:
    False, sin línea, las dos siguen con enviada_whatsapp False (no se tocan).
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_f@test.com", telefono="+5491100000006")
    n_ant = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-10-01", "2026-10-31", "40000")
    n_vieja = _crear_notificacion_proyeccion(db, u.id, datos_template={})

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    with buzon_de_respuesta(u.telefono):
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    db.refresh(n_ant)
    db.refresh(n_vieja)
    assert n_ant.enviada_whatsapp is False
    assert n_vieja.enviada_whatsapp is False


def test_caso_g_canal_whatsapp_false(db: Session, monkeypatch):
    """
    (g) canal_whatsapp False: False.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_g@test.com", telefono="+5491100000007")
    notif = _crear_notificacion_proyeccion(
        db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000", canal_whatsapp=False
    )

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )
    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    with buzon_de_respuesta(u.telefono):
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    db.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_h_propuesta_pendiente(db: Session, monkeypatch):
    """
    (h) Con propuesta pendiente: False y sigue pendiente.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_h@test.com", telefono="+5491100000008")
    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )
    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    # Mock de propuesta pendiente
    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp._buscar_propuesta_pendiente",
        lambda uid, db_s: True,
    )

    with buzon_de_respuesta(u.telefono):
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    db.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_i_m_dos_pendientes_ciclo(db: Session, monkeypatch):
    """
    (i) M con dos pendientes del ciclo (ARS y USD) y libres −40.000 y −500:
    el primer mensaje lleva la de ARS ("...$40.000...") y el segundo "Ojo: en dólares te faltan US$500 para cubrir lo que vence hasta el 30/11.".
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_i@test.com", telefono="+5491100000009", moneda_secundaria_activa=True)
    n_ars = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")
    n_usd = _crear_notificacion_proyeccion(db, u.id, "USD", "2026-11-01", "2026-11-30", "500")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-40000"),
        libre_usd=Decimal("-500"),
        datos_viejos=False,
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    # Primer mensaje: sale ARS
    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Msg 1.")
        ok1 = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok1 is True

    assert mock_enviar.call_count == 1
    texto_1 = mock_enviar.call_args[1].get("texto")
    assert texto_1 == "Msg 1.\nOjo: con lo que tenés hoy te faltan $40.000 para cubrir lo que vence hasta el 30/11."
    db.refresh(n_ars)
    db.refresh(n_usd)
    assert n_ars.enviada_whatsapp is True
    assert n_usd.enviada_whatsapp is False

    # Segundo mensaje: sale USD
    mock_enviar.reset_mock()
    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Msg 2.")
        ok2 = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok2 is True

    assert mock_enviar.call_count == 1
    texto_2 = mock_enviar.call_args[1].get("texto")
    assert texto_2 == "Msg 2.\nOjo: en dólares te faltan US$500 para cubrir lo que vence hasta el 30/11."
    db.refresh(n_usd)
    assert n_usd.enviada_whatsapp is True


def test_caso_j_envio_falla_notificacion_sigue_pendiente(db: Session, monkeypatch):
    """
    (j) _enviar_registrado falla: la notificación sigue pendiente.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_j@test.com", telefono="+5491100000010")
    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    # Simular fallo en _enviar_registrado
    mock_enviar = MagicMock(return_value=False)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Mensaje.")
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is True

    db.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_k_estatico_ast_whatsapp_ia():
    """
    (k) Estático (ast sobre app/routers/whatsapp_ia.py):
    agregar_aviso_fin_de_mes se llama una sola vez y en una línea posterior a
    agregar_aviso_personas, que es posterior a agregar_recordatorio_impago.
    """
    file_path = Path("app/routers/whatsapp_ia.py")
    tree = ast.parse(file_path.read_text(encoding="utf-8"))

    llamadas: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in ("agregar_recordatorio_impago", "agregar_aviso_personas", "agregar_aviso_fin_de_mes"):
                llamadas.append((name, node.lineno))

    fin_de_mes_calls = [l for l in llamadas if l[0] == "agregar_aviso_fin_de_mes"]
    assert len(fin_de_mes_calls) == 1, f"agregar_aviso_fin_de_mes debe llamarse 1 sola vez, encontrada {len(fin_de_mes_calls)}"

    linea_fin_de_mes = fin_de_mes_calls[0][1]
    linea_personas = next(l[1] for l in llamadas if l[0] == "agregar_aviso_personas")
    linea_impago = next(l[1] for l in llamadas if l[0] == "agregar_recordatorio_impago")

    assert linea_impago < linea_personas < linea_fin_de_mes, (
        f"Orden incorrecto: impago={linea_impago}, personas={linea_personas}, fin_de_mes={linea_fin_de_mes}"
    )


def test_caso_l_otro_usuario_no_hace_salir_linea(db: Session, monkeypatch):
    """
    (l) Otro usuario con la misma notificación no hace salir la línea de P.
    """
    hoy = date(2026, 11, 5)
    u_p = _crear_usuario_test(db, "u_p_l@test.com", telefono="+5491100000011")
    u_otro = _crear_usuario_test(db, "u_otro_l@test.com", telefono="+5491100000012")

    # Notificación pertenece a u_otro, NO a u_p
    _crear_notificacion_proyeccion(db, u_otro.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    with buzon_de_respuesta(u_p.telefono):
        ok = agregar_aviso_fin_de_mes(db, u_p, hoy=hoy)
        assert ok is False


def test_caso_m_sin_compromisos(db: Session, monkeypatch):
    """
    (m) pendiente ARS del ciclo, libre de hoy −50.000 pero sin compromisos:
    sin línea y la notificación queda descartada (enviada_whatsapp True).
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_m_comp@test.com", telefono="+5491100000013")
    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
        compromisos_ars=[],
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Listo.")
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[1].get("texto") == "Listo."

    db.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_caso_n_billetera_sin_saldo_cargado(db: Session, monkeypatch):
    """
    (n) pendiente ARS del ciclo, libre −50.000 con compromiso, pero la billetera
    ya no tiene saldo cargado (sin punto de partida ni control) -> sin línea y descartada.
    """
    hoy = date(2026, 11, 5)
    u = _crear_usuario_test(db, "u_n_saldo@test.com", telefono="+5491100000014")
    for b in db.query(Billetera).filter(Billetera.usuario_id == u.id).all():
        b.tiene_punto_partida = False
        b.fecha_ultimo_control = None
    db.commit()

    notif = _crear_notificacion_proyeccion(db, u.id, "ARS", "2026-11-01", "2026-11-30", "40000")

    resultado_mock = DummyResultadoFinDeMes(
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        libre_ars=Decimal("-50000"),
        libre_usd=Decimal("0"),
        datos_viejos=False,
        compromisos_ars=[{"tipo": "suscripcion", "monto": Decimal("1000"), "fecha": date(2026, 11, 20)}],
    )

    monkeypatch.setattr(
        "app.routers.whatsapp.aviso_fin_de_mes_wpp.calcular_fin_de_mes",
        lambda *a, **kw: resultado_mock,
    )

    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(u.telefono):
        enviar_whatsapp(u.telefono, "Listo.")
        ok = agregar_aviso_fin_de_mes(db, u, hoy=hoy)
        assert ok is False

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[1].get("texto") == "Listo."

    db.refresh(notif)
    assert notif.enviada_whatsapp is True
