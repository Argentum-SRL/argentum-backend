"""
tests/test_recordatorio_impago_wpp.py — Pruebas del recordatorio de resumen impago por WhatsApp
como aviso al pie del mensaje de respuesta (Casos A a M).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if not hasattr(PGUUID, "_compiler_dispatcher") or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {}):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if not hasattr(JSONB, "_compiler_dispatcher") or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {}):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.recordatorio_impago_wpp import agregar_recordatorio_impago
from app.services import whatsapp_service
from app.services.whatsapp_service import buzon_de_respuesta, enviar_whatsapp


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.whatsapp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("time.sleep", lambda s: None)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario_test(db: Session, telefono: str = "+5491112345678") -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        telefono=telefono,
        telefono_normalizado=telefono,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Juan",
        apellido="Pérez",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _crear_tarjeta(db: Session, usuario_id: UUID, nombre: str = "Visa") -> TarjetaCredito:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre=f"Cuenta {nombre}",
        moneda=Moneda.ARS,
    )
    db.add(b)
    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=b.id,
        nombre=nombre,
        red=RedTarjeta.VISA if "visa" in nombre.lower() else RedTarjeta.MASTERCARD,
        estado=EstadoTarjeta.ACTIVA,
        dia_cierre=20,
        dia_vencimiento=4,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _crear_notificacion_resumen(
    db: Session,
    usuario_id: UUID,
    tarjeta_nombre: str = "Visa",
    vencimiento_str: str = "2026-11-04",
    archivada: bool = False,
    enviada: bool = False,
    dias_antes_vencimiento: int = 0,
) -> Notificacion:
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.usuario_id == usuario_id,
        TarjetaCredito.nombre == tarjeta_nombre,
    ).first()
    entidad_id = tarjeta.id if tarjeta else None
    n = Notificacion(
        id=uuid4(),
        usuario_id=usuario_id,
        tipo=TipoNotificacion.RESUMEN_IMPAGO,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje=f"Resumen {tarjeta_nombre} impago",
        canal_whatsapp=True,
        enviada_whatsapp=enviada,
        archivada=archivada,
        entidad_tipo="tarjeta",
        entidad_id=entidad_id,
        datos_template={
            "tarjeta_nombre": tarjeta_nombre,
            "vencimiento": vencimiento_str,
            "dias_antes_vencimiento": dias_antes_vencimiento,
        },
        created_at=datetime.now(timezone.utc),
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


def test_caso_a_un_resumen_visa(db_session, monkeypatch):
    """A 1 resumen Visa, deuda 50000, vence 04/11 →
    aviso "Ojo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar.";
    al despachar, notificación enviada_whatsapp=True."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    notif = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("50000"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is True

    assert mock_enviar.call_count == 1
    texto_saliente = mock_enviar.call_args[1].get("texto")
    aviso_esperado = "Ojo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar."
    assert texto_saliente == f"Listo.\n{aviso_esperado}"

    db_session.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_caso_b_se_llama_de_nuevo_despues_de_despachar(db_session, monkeypatch):
    """B Se llama de nuevo después de despachar → False, sin aviso."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("50000"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        agregar_recordatorio_impago(db_session, usuario)

    # Segundo llamado con buzón nuevo: no hay resúmenes pendientes
    mock_enviar.reset_mock()
    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Hola.")
        res2 = agregar_recordatorio_impago(db_session, usuario)
        assert res2 is False

    assert mock_enviar.call_args[1].get("texto") == "Hola."
    assert mock_enviar.call_args[1].get("aviso_tipo") is None


def test_caso_c_archivada_sin_aviso(db_session, monkeypatch):
    """C Archivada → sin aviso."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04", archivada=True)

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("50000"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is False

    assert mock_enviar.call_args[1].get("texto") == "Listo."


def test_caso_d_deuda_cero_sin_aviso_y_marcada(db_session, monkeypatch):
    """D Deuda 0 → sin aviso y marcada."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    notif = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    # obtener_detalle devuelve deuda 0
    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("0"), "ARS", True, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is False

    assert mock_enviar.call_args[1].get("texto") == "Listo."
    db_session.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_caso_e_propuesta_pendiente_sin_aviso_y_sin_marcar(db_session, monkeypatch):
    """E Propuesta pendiente → sin aviso y sin marcar."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    notif = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp._buscar_propuesta_pendiente",
        lambda uid, db: True,
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is False

    assert mock_enviar.call_args[1].get("texto") == "Listo."
    db_session.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_f_dos_resumenes_distintos(db_session, monkeypatch):
    """F Mastercard deuda 20000 vence 20/10 y Visa deuda 50000 vence 04/11
    → "Ojo: tenés 2 resúmenes de tarjeta sin pagar; el más viejo venció el 20/10."; las dos marcadas."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Mastercard")
    _crear_tarjeta(db_session, usuario.id, "Visa")
    n1 = _crear_notificacion_resumen(db_session, usuario.id, "Mastercard", "2026-10-20")
    n2 = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    def _mock_detalle(db, tarjeta_id, venc, *args, **kwargs):
        tarj = db.query(TarjetaCredito).filter(TarjetaCredito.id == tarjeta_id).first()
        if tarj and "mastercard" in tarj.nombre.lower():
            return (Decimal("20000"), "ARS", False, False)
        return (Decimal("50000"), "ARS", False, False)

    monkeypatch.setattr("app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento", _mock_detalle)
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is True

    texto_saliente = mock_enviar.call_args[1].get("texto")
    aviso_esperado = "Ojo: tenés 2 resúmenes de tarjeta sin pagar; el más viejo venció el 20/10."
    assert texto_saliente == f"Listo.\n{aviso_esperado}"

    db_session.refresh(n1)
    db_session.refresh(n2)
    assert n1.enviada_whatsapp is True
    assert n2.enviada_whatsapp is True


def test_caso_g_mismo_resumen_notificaciones_multiples(db_session, monkeypatch):
    """G Mismo resumen con notificaciones d=0 y d=3 → el texto de A; las dos marcadas."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    n1 = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04", dias_antes_vencimiento=0)
    n2 = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04", dias_antes_vencimiento=3)

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("50000"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is True

    texto_saliente = mock_enviar.call_args[1].get("texto")
    aviso_esperado = "Ojo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar."
    assert texto_saliente == f"Listo.\n{aviso_esperado}"

    db_session.refresh(n1)
    db_session.refresh(n2)
    assert n1.enviada_whatsapp is True
    assert n2.enviada_whatsapp is True


def test_caso_h_cuatro_resumenes(db_session, monkeypatch):
    """H 4 resúmenes (01/10, 10/10, 20/10, 05/11) →
    "Ojo: tenés 4 resúmenes de tarjeta sin pagar; el más viejo venció el 01/10."; las 4 marcadas."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Tarjeta1")
    _crear_tarjeta(db_session, usuario.id, "Tarjeta2")
    _crear_tarjeta(db_session, usuario.id, "Tarjeta3")
    _crear_tarjeta(db_session, usuario.id, "Tarjeta4")

    n1 = _crear_notificacion_resumen(db_session, usuario.id, "Tarjeta1", "2026-10-01")
    n2 = _crear_notificacion_resumen(db_session, usuario.id, "Tarjeta2", "2026-10-10")
    n3 = _crear_notificacion_resumen(db_session, usuario.id, "Tarjeta3", "2026-10-20")
    n4 = _crear_notificacion_resumen(db_session, usuario.id, "Tarjeta4", "2026-11-05")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("10000"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is True

    texto_saliente = mock_enviar.call_args[1].get("texto")
    aviso_esperado = "Ojo: tenés 4 resúmenes de tarjeta sin pagar; el más viejo venció el 01/10."
    assert texto_saliente == f"Listo.\n{aviso_esperado}"

    for n in [n1, n2, n3, n4]:
        db_session.refresh(n)
        assert n.enviada_whatsapp is True


def test_caso_i_obtener_detalle_lanza_excepcion(db_session, monkeypatch):
    """I obtener_detalle_resumen_vencimiento lanza → False, sin marcar, no propaga."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    notif = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    def _falla(*args, **kwargs):
        raise RuntimeError("Error en cálculo de resumen")

    monkeypatch.setattr("app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento", _falla)
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is False

    assert mock_enviar.call_args[1].get("texto") == "Listo."
    db_session.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_j_enviar_registrado_falla_o_lanza(db_session, monkeypatch):
    """J _enviar_registrado devuelve False → sin marcar; _enviar_registrado lanza → sin marcar y no propaga."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    notif = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("50000"), "ARS", False, False),
    )

    # 1. _enviar_registrado devuelve False
    mock_enviar_false = MagicMock(return_value=False)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar_false)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        agregar_recordatorio_impago(db_session, usuario)

    db_session.refresh(notif)
    assert notif.enviada_whatsapp is False

    # 2. _enviar_registrado lanza
    def _lanza(*args, **kwargs):
        raise ConnectionError("Falla de red")

    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", _lanza)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        agregar_recordatorio_impago(db_session, usuario)

    db_session.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_k_recorte_nombre_largo(db_session, monkeypatch):
    """K Tarjeta "Mastercard Black Banco Galicia Platinum" con deuda 1234567.89 vence 04/11 →
    "Ojo: el resumen de Mastercard Black… de $1.234.567,89 (venció 04/11) figura sin pagar." (86 caracteres)."""
    usuario = _crear_usuario_test(db_session)
    nombre_largo = "Mastercard Black Banco Galicia Platinum"
    _crear_tarjeta(db_session, usuario.id, nombre_largo)
    _crear_notificacion_resumen(db_session, usuario.id, nombre_largo, "2026-11-04")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("1234567.89"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        enviar_whatsapp(usuario.telefono, "Listo.")
        res = agregar_recordatorio_impago(db_session, usuario)
        assert res is True

    texto_saliente = mock_enviar.call_args[1].get("texto")
    aviso_esperado = "Ojo: el resumen de Mastercard Black… de $1.234.567,89 (venció 04/11) figura sin pagar."
    assert len(aviso_esperado) == 86
    assert texto_saliente == f"Listo.\n{aviso_esperado}"


def test_caso_l_sin_buzon_abierto(db_session, monkeypatch):
    """L Sin buzón abierto → False y nada se manda ni se marca."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    notif = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("50000"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    res = agregar_recordatorio_impago(db_session, usuario)
    assert res is False
    assert mock_enviar.call_count == 0

    db_session.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_m_buzon_sin_respuesta_no_manda_ni_marca(db_session, monkeypatch):
    """M Buzón sin respuesta → no se manda nada y no se marca."""
    usuario = _crear_usuario_test(db_session)
    _crear_tarjeta(db_session, usuario.id, "Visa")
    notif = _crear_notificacion_resumen(db_session, usuario.id, "Visa", "2026-11-04")

    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: (Decimal("50000"), "ARS", False, False),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta(usuario.telefono):
        # NO se llama a enviar_whatsapp -> buzon no tiene partes
        agregar_recordatorio_impago(db_session, usuario)

    assert mock_enviar.call_count == 0
    db_session.refresh(notif)
    assert notif.enviada_whatsapp is False
