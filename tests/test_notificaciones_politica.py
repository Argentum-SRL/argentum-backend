"""
Pruebas para política de notificaciones de WhatsApp (D2):
1. plantillas_activas() con la variable por defecto == {"resumen_ciclo","alerta_cambio_email","alerta_cambio_contrasena"}.
2. actualizar_password de un usuario con teléfono -> 1 llamada con "alerta_cambio_contrasena", "es", [] y max_intentos=1; la notificación queda enviada_whatsapp=True.
3. Segundo cambio de contraseña el mismo día -> 2 notificaciones CAMBIO_CONTRASENA y 2 llamadas en total.
4. actualizar_email con el mock devolviendo False -> notificación pendiente; _job_reintentar_inmediatas con ahora = creación + 3 minutos -> 1 llamada más con "alerta_cambio_email" y parámetro el email nuevo; con ahora = creación + 20 minutos -> 0 llamadas.
5. Job con usuario a las 9:00 y ahora 09:03: RESUMEN_CICLO pendiente creada hace 5 horas -> 1 envío "resumen_ciclo"; nueva corrida a las 09:04 -> 0.
6. Mismo job: PRESUPUESTO_LIMITE, SALDO_CERO y CAMBIO_CONTRASENA pendientes con canal_whatsapp=True -> 0 envíos y las 3 siguen enviada_whatsapp=False.
7. RESUMEN_CICLO creada hace 73 horas -> 0 envíos.
8. Usuario a las 10:00 y ahora 09:03 -> 0 envíos; usuario a las 8:00 y ahora 09:03 -> 0 envíos (pasaron más de 30 minutos).
9. Con WHATSAPP_PLANTILLAS_ACTIVAS="resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena,alerta_presupuesto_limite" -> la PRESUPUESTO_LIMITE del caso 6 sale (1 envío "alerta_presupuesto_limite").
10. GET /notificaciones/configuracion: con la variable por defecto whatsapp_tipos_activos == ["resumen_ciclo"]; con la del caso 9 == ["presupuesto_umbral_1","resumen_ciclo"].
11. enviar_whatsapp_template con timeout simulado: con max_intentos=1 -> 1 intento; sin el parámetro -> 3.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine, types
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


from app.core.config import settings
from app.core.database import Base
from app.core.politica_notificaciones import plantillas_activas
from app.core.security import get_password_hash
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.usuario import AuthProvider, Moneda, Usuario
from app.routers.notificaciones import obtener_configuracion
from app.schemas.usuario import EditarEmail, EditarPassword
from app.services.notificacion_despacho_service import _job_reintentar_inmediatas
from app.services.notificacion_scheduler_service import (
    _job_entrega_whatsapp_batched,
    _ultimo_intento_whatsapp,
)
from app.services.usuario_service import actualizar_email, actualizar_password
from app.services.whatsapp_service import enviar_whatsapp_template

TZ_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")


# Adaptador SQLite para UUID
@pytest.fixture(autouse=True)
def setup_sqlite_uuid(monkeypatch):
    orig_bind = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        if dialect.name == "sqlite":
            return lambda value: value.hex if isinstance(value, uuid.UUID) else (uuid.UUID(str(value)).hex if value else None)
        return orig_bind(self, dialect)

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)
    yield


@pytest.fixture
def session_factory():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def db_session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def mock_wpp():
    with patch("app.services.whatsapp_service.enviar_whatsapp_template") as m_tpl, \
         patch("app.services.whatsapp_service.enviar_whatsapp") as m_raw, \
         patch("app.services.notificacion_despacho_service.enviar_whatsapp_template", m_tpl), \
         patch("app.services.notificacion_despacho_service.enviar_whatsapp", m_raw), \
         patch("app.services.notificacion_scheduler_service.enviar_whatsapp_template", m_tpl), \
         patch("app.services.email_service.generar_y_enviar_verificacion_email", return_value=True), \
         patch("app.services.notificacion_email_service.enviar_email_notificacion", return_value=True):
        m_tpl.return_value = True
        m_raw.return_value = True
        yield {"template": m_tpl, "raw": m_raw}


def _crear_usuario_test(db, email=None, telefono=None, hora=9, minuto=0):
    uid = uuid.uuid4()
    if email is None:
        email = f"user_{uid.hex[:8]}@test.com"
    if telefono is None:
        telefono = f"+54911{uid.int % 100000000:08d}"

    usuario = Usuario(
        id=uid,
        email=email,
        nombre="Test",
        apellido="User",
        telefono=telefono,
        password_configurada=True,
        password_hash=get_password_hash("Password123!"),
        moneda_principal=Moneda.ARS,
        auth_provider=AuthProvider.EMAIL,
        onboarding_completo=True,
    )
    db.add(usuario)
    db.flush()

    cfg = ConfiguracionNotificacion(
        id=uuid.uuid4(),
        usuario_id=usuario.id,
        whatsapp_hora_envio=hora,
        whatsapp_minuto_envio=minuto,
    )
    db.add(cfg)
    db.commit()
    return usuario


# =============================================================================
# CASOS DE PRUEBA ESPECIFICADOS EN D2
# =============================================================================

def test_01_plantillas_activas_defecto():
    """1. plantillas_activas() con la variable por defecto == {'resumen_ciclo','alerta_cambio_email','alerta_cambio_contrasena'}."""
    activas = plantillas_activas()
    assert activas == {"resumen_ciclo", "alerta_cambio_email", "alerta_cambio_contrasena"}


def test_02_actualizar_password_inmediata(db_session, mock_wpp):
    """2. actualizar_password de un usuario con teléfono -> 1 llamada con 'alerta_cambio_contrasena', 'es', [] y max_intentos=1; notif queda enviada_whatsapp=True."""
    usuario = _crear_usuario_test(db_session)
    datos = EditarPassword(
        password_actual="Password123!",
        password_nueva="NuevaPassword123!",
        password_nueva_confirmacion="NuevaPassword123!",
    )

    actualizar_password(db_session, usuario, datos)

    assert mock_wpp["template"].call_count == 1
    args, kwargs = mock_wpp["template"].call_args
    # enviar_whatsapp_template(usuario.telefono, plantilla, "es", componentes(valores), max_intentos=1)
    assert args[0] == usuario.telefono
    assert args[1] == "alerta_cambio_contrasena"
    assert args[2] == "es"
    assert args[3] == []
    assert kwargs.get("max_intentos") == 1 or (len(args) > 4 and args[4] == 1)

    notif = db_session.query(Notificacion).filter(
        Notificacion.usuario_id == usuario.id,
        Notificacion.tipo == TipoNotificacion.CAMBIO_CONTRASENA,
    ).first()
    assert notif is not None
    assert notif.enviada_whatsapp is True


def test_03_segundo_cambio_password_mismo_dia(db_session, mock_wpp):
    """3. Segundo cambio de contraseña el mismo día -> 2 notificaciones CAMBIO_CONTRASENA y 2 llamadas en total."""
    usuario = _crear_usuario_test(db_session)
    datos1 = EditarPassword(
        password_actual="Password123!",
        password_nueva="NuevaPass123!",
        password_nueva_confirmacion="NuevaPass123!",
    )
    actualizar_password(db_session, usuario, datos1)

    datos2 = EditarPassword(
        password_actual="NuevaPass123!",
        password_nueva="OtraPass123!",
        password_nueva_confirmacion="OtraPass123!",
    )
    actualizar_password(db_session, usuario, datos2)

    assert mock_wpp["template"].call_count == 2
    notifs = db_session.query(Notificacion).filter(
        Notificacion.usuario_id == usuario.id,
        Notificacion.tipo == TipoNotificacion.CAMBIO_CONTRASENA,
    ).all()
    assert len(notifs) == 2
    assert all(n.enviada_whatsapp is True for n in notifs)


def test_04_reintento_inmediatas_ventana(session_factory, mock_wpp, monkeypatch):
    """4. actualizar_email con el mock devolviendo False -> notificación pendiente;
    _job_reintentar_inmediatas con ahora = creación + 3 minutos -> 1 llamada más con 'alerta_cambio_email' y parámetro el email nuevo;
    con ahora = creación + 20 minutos -> 0 llamadas."""
    with session_factory() as s:
        usuario = _crear_usuario_test(s)
        usuario_id = usuario.id
        mock_wpp["template"].return_value = False

        datos = EditarEmail(email_nuevo="nuevo@example.com", password_actual="Password123!")
        actualizar_email(s, usuario, datos)

        notif = s.query(Notificacion).filter(
            Notificacion.usuario_id == usuario_id,
            Notificacion.tipo == TipoNotificacion.CAMBIO_EMAIL,
        ).first()
        assert notif is not None
        assert notif.enviada_whatsapp is False
        assert mock_wpp["template"].call_count == 1
        notif_id = notif.id
        created_at = notif.created_at

    # Mockear que el template responderá True en el reintento
    mock_wpp["template"].return_value = True

    # Caso ahora = creación + 3 minutos -> 1 llamada más
    ahora_3m = created_at + timedelta(minutes=3)
    if ahora_3m.tzinfo is None:
        ahora_3m = ahora_3m.replace(tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.notificacion_despacho_service.ahora_argentina", lambda: ahora_3m)

    _job_reintentar_inmediatas(session_factory)
    assert mock_wpp["template"].call_count == 2

    # Verificar argumentos del reintento
    args, kwargs = mock_wpp["template"].call_args
    assert args[1] == "alerta_cambio_email"
    assert args[3] == [{"type": "body", "parameters": [{"type": "text", "text": "nuevo@example.com"}]}]

    with session_factory() as s:
        notif_actualizada = s.query(Notificacion).filter_by(id=notif_id).first()
        assert notif_actualizada.enviada_whatsapp is True

    # Segundo escenario: creación + 20 minutos -> 0 llamadas
    with session_factory() as s:
        notif2 = Notificacion(
            id=uuid.uuid4(),
            usuario_id=usuario_id,
            tipo=TipoNotificacion.CAMBIO_EMAIL,
            nivel=NivelNotificacion.CRITICA,
            mensaje="Email actualizado",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"email": "otro@example.com"},
            created_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
        s.add(notif2)
        s.commit()
        notif2_created_at = notif2.created_at

    mock_wpp["template"].reset_mock()
    ahora_20m = notif2_created_at + timedelta(minutes=20)
    if ahora_20m.tzinfo is None:
        ahora_20m = ahora_20m.replace(tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.notificacion_despacho_service.ahora_argentina", lambda: ahora_20m)

    _job_reintentar_inmediatas(session_factory)
    assert mock_wpp["template"].call_count == 0


def test_05_job_programado_resumen_ciclo(session_factory, mock_wpp, monkeypatch):
    """5. Job con usuario a las 9:00 y ahora 09:03: RESUMEN_CICLO pendiente creada hace 5 horas -> 1 envío 'resumen_ciclo'; nueva corrida a las 09:04 -> 0."""
    _ultimo_intento_whatsapp.clear()
    ahora_0903 = datetime(2026, 10, 9, 9, 3, tzinfo=TZ_ARGENTINA)

    with session_factory() as s:
        usuario = _crear_usuario_test(s, hora=9, minuto=0)
        notif = Notificacion(
            id=uuid.uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.RESUMEN_CICLO,
            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
            mensaje="Resumen de ciclo",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"ingresos": "100", "egresos": "50", "balance": "50", "top_categoria": "Comida"},
            created_at=ahora_0903 - timedelta(hours=5),
        )
        s.add(notif)
        s.commit()
        notif_id = notif.id

    monkeypatch.setattr("app.services.notificacion_scheduler_service.ahora_argentina", lambda: ahora_0903)
    mock_wpp["template"].reset_mock()

    _job_entrega_whatsapp_batched(session_factory)
    assert mock_wpp["template"].call_count == 1
    args, _ = mock_wpp["template"].call_args
    assert args[1] == "resumen_ciclo"

    with session_factory() as s:
        notif_despachada = s.query(Notificacion).filter_by(id=notif_id).first()
        assert notif_despachada.enviada_whatsapp is True

    # Nueva corrida a las 09:04 -> 0 envíos
    ahora_0904 = datetime(2026, 10, 9, 9, 4, tzinfo=TZ_ARGENTINA)
    monkeypatch.setattr("app.services.notificacion_scheduler_service.ahora_argentina", lambda: ahora_0904)
    mock_wpp["template"].reset_mock()

    _job_entrega_whatsapp_batched(session_factory)
    assert mock_wpp["template"].call_count == 0


def test_06_job_omite_tipos_pausados_e_inmediatos(session_factory, mock_wpp, monkeypatch):
    """6. Mismo job: PRESUPUESTO_LIMITE, SALDO_CERO y CAMBIO_CONTRASENA pendientes con canal_whatsapp=True -> 0 envíos y las 3 siguen enviada_whatsapp=False."""
    _ultimo_intento_whatsapp.clear()
    ahora = datetime(2026, 10, 9, 9, 3, tzinfo=TZ_ARGENTINA)

    with session_factory() as s:
        usuario = _crear_usuario_test(s, hora=9, minuto=0)
        notif_pres = Notificacion(
            id=uuid.uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.PRESUPUESTO_LIMITE,
            nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
            mensaje="Presupuesto al límite",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"gastado_fmt": "$80", "limite_fmt": "$100", "nombre_pres": "Comida"},
            created_at=ahora - timedelta(hours=1),
        )
        notif_saldo = Notificacion(
            id=uuid.uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.SALDO_CERO,
            nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
            mensaje="Saldo cero",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"billetera_nombre": "Efectivo"},
            created_at=ahora - timedelta(hours=1),
        )
        notif_pass = Notificacion(
            id=uuid.uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.CAMBIO_CONTRASENA,
            nivel=NivelNotificacion.CRITICA,
            mensaje="Cambio pass",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            created_at=ahora - timedelta(hours=1),
        )
        s.add_all([notif_pres, notif_saldo, notif_pass])
        s.commit()
        id_pres = notif_pres.id
        id_saldo = notif_saldo.id
        id_pass = notif_pass.id

    monkeypatch.setattr("app.services.notificacion_scheduler_service.ahora_argentina", lambda: ahora)
    mock_wpp["template"].reset_mock()

    _job_entrega_whatsapp_batched(session_factory)
    assert mock_wpp["template"].call_count == 0

    with session_factory() as s:
        assert s.query(Notificacion).filter_by(id=id_pres).first().enviada_whatsapp is False
        assert s.query(Notificacion).filter_by(id=id_saldo).first().enviada_whatsapp is False
        assert s.query(Notificacion).filter_by(id=id_pass).first().enviada_whatsapp is False


def test_07_job_omite_mayores_a_72_horas(session_factory, mock_wpp, monkeypatch):
    """7. RESUMEN_CICLO creada hace 73 horas -> 0 envíos."""
    _ultimo_intento_whatsapp.clear()
    ahora = datetime(2026, 10, 9, 9, 3, tzinfo=TZ_ARGENTINA)

    with session_factory() as s:
        usuario = _crear_usuario_test(s, hora=9, minuto=0)
        notif = Notificacion(
            id=uuid.uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.RESUMEN_CICLO,
            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
            mensaje="Resumen viejo",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"ingresos": "100", "egresos": "50", "balance": "50", "top_categoria": "Comida"},
            created_at=ahora - timedelta(hours=73),
        )
        s.add(notif)
        s.commit()
        notif_id = notif.id

    monkeypatch.setattr("app.services.notificacion_scheduler_service.ahora_argentina", lambda: ahora)
    mock_wpp["template"].reset_mock()

    _job_entrega_whatsapp_batched(session_factory)
    assert mock_wpp["template"].call_count == 0

    with session_factory() as s:
        assert s.query(Notificacion).filter_by(id=notif_id).first().enviada_whatsapp is False


def test_08_job_ventana_horaria_usuario(session_factory, mock_wpp, monkeypatch):
    """8. Usuario a las 10:00 y ahora 09:03 -> 0 envíos; usuario a las 8:00 y ahora 09:03 -> 0 envíos (pasaron más de 30 minutos)."""
    _ultimo_intento_whatsapp.clear()
    ahora = datetime(2026, 10, 9, 9, 3, tzinfo=TZ_ARGENTINA)

    with session_factory() as s:
        u10 = _crear_usuario_test(s, email="u10@test.com", hora=10, minuto=0)
        u8 = _crear_usuario_test(s, email="u8@test.com", hora=8, minuto=0)

        n10 = Notificacion(
            id=uuid.uuid4(),
            usuario_id=u10.id,
            tipo=TipoNotificacion.RESUMEN_CICLO,
            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
            mensaje="Resumen u10",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"ingresos": "100", "egresos": "50", "balance": "50", "top_categoria": "Comida"},
            created_at=ahora - timedelta(hours=2),
        )
        n8 = Notificacion(
            id=uuid.uuid4(),
            usuario_id=u8.id,
            tipo=TipoNotificacion.RESUMEN_CICLO,
            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
            mensaje="Resumen u8",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"ingresos": "100", "egresos": "50", "balance": "50", "top_categoria": "Comida"},
            created_at=ahora - timedelta(hours=2),
        )
        s.add_all([n10, n8])
        s.commit()

    monkeypatch.setattr("app.services.notificacion_scheduler_service.ahora_argentina", lambda: ahora)
    mock_wpp["template"].reset_mock()

    _job_entrega_whatsapp_batched(session_factory)
    assert mock_wpp["template"].call_count == 0


def test_09_reactivacion_plantilla_presupuesto(session_factory, mock_wpp, monkeypatch):
    """9. Con WHATSAPP_PLANTILLAS_ACTIVAS con alerta_presupuesto_limite -> la PRESUPUESTO_LIMITE del caso 6 sale (1 envío)."""
    _ultimo_intento_whatsapp.clear()
    ahora = datetime(2026, 10, 9, 9, 3, tzinfo=TZ_ARGENTINA)

    with session_factory() as s:
        usuario = _crear_usuario_test(s, hora=9, minuto=0)
        notif_pres = Notificacion(
            id=uuid.uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.PRESUPUESTO_LIMITE,
            nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
            mensaje="Presupuesto al límite",
            canal_whatsapp=True,
            enviada_whatsapp=False,
            datos_template={"gastado_fmt": "$80", "limite_fmt": "$100", "nombre_pres": "Comida"},
            created_at=ahora - timedelta(hours=1),
        )
        s.add(notif_pres)
        s.commit()
        pres_id = notif_pres.id

    monkeypatch.setattr(settings, "WHATSAPP_PLANTILLAS_ACTIVAS", "resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena,alerta_presupuesto_limite")
    monkeypatch.setattr("app.services.notificacion_scheduler_service.ahora_argentina", lambda: ahora)
    mock_wpp["template"].reset_mock()

    _job_entrega_whatsapp_batched(session_factory)
    assert mock_wpp["template"].call_count == 1
    args, _ = mock_wpp["template"].call_args
    assert args[1] == "alerta_presupuesto_limite"

    with session_factory() as s:
        assert s.query(Notificacion).filter_by(id=pres_id).first().enviada_whatsapp is True


def test_10_endpoint_configuracion_whatsapp_tipos_activos(db_session, monkeypatch):
    """10. GET /notificaciones/configuracion: con defecto == ['resumen_ciclo']; con caso 9 == ['presupuesto_umbral_1','resumen_ciclo']."""
    usuario = _crear_usuario_test(db_session)

    # Por defecto
    monkeypatch.setattr(settings, "WHATSAPP_PLANTILLAS_ACTIVAS", "resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena")
    res_def = obtener_configuracion(db=db_session, usuario=usuario)
    assert res_def.whatsapp_tipos_activos == ["resumen_ciclo"]

    # Con caso 9
    monkeypatch.setattr(settings, "WHATSAPP_PLANTILLAS_ACTIVAS", "resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena,alerta_presupuesto_limite")
    res_caso9 = obtener_configuracion(db=db_session, usuario=usuario)
    assert res_caso9.whatsapp_tipos_activos == ["presupuesto_umbral_1", "resumen_ciclo"]


def test_11_timeout_reintentos_enviar_whatsapp_template(monkeypatch):
    """11. enviar_whatsapp_template con timeout simulado: con max_intentos=1 -> 1 intento; sin el parámetro -> 3."""
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "fake_token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "fake_id")
    monkeypatch.setattr("time.sleep", lambda s: None)

    mock_client = MagicMock()
    mock_client.post.side_effect = httpx.TimeoutException("Timeout")
    monkeypatch.setattr("app.services.whatsapp_service.get_meta_http_client", lambda: mock_client)

    # Con max_intentos=1
    res1 = enviar_whatsapp_template("+5491112345678", "template_test", "es", [], max_intentos=1)
    assert res1 is False
    assert mock_client.post.call_count == 1

    # Sin el parámetro -> 3 intentos
    mock_client.post.reset_mock()
    res3 = enviar_whatsapp_template("+5491112345678", "template_test", "es", [])
    assert res3 is False
    assert mock_client.post.call_count == 3


# =============================================================================
# CASOS DE PRUEBA ADICIONALES (FASE_E1_NOTIF2: TESTS 12 A 15)
# =============================================================================

from decimal import Decimal
from types import SimpleNamespace
from app.services.presupuesto_service import verificar_alertas_presupuesto


def test_12_usuario_sin_telefono_no_reintenta_ni_alerta(session_factory, mock_wpp, monkeypatch):
    """12. Usuario creado con _crear_usuario_test y después telefono=None (commit).
    actualizar_password -> 0 llamadas a la plantilla.
    _job_reintentar_inmediatas con ahora = creación + 3 minutos -> 0 llamadas a la plantilla.
    Con ahora = creación + 16 minutos -> app.services.notificacion_despacho_service.enviar_alerta_admin con 0 llamadas."""
    mock_alerta = MagicMock()
    monkeypatch.setattr("app.services.notificacion_despacho_service.enviar_alerta_admin", mock_alerta)
    mock_wpp["template"].reset_mock()

    with session_factory() as s:
        usuario = _crear_usuario_test(s)
        usuario.telefono = None
        s.commit()
        usuario_id = usuario.id

        datos = EditarPassword(
            password_actual="Password123!",
            password_nueva="NuevaPassword123!",
            password_nueva_confirmacion="NuevaPassword123!",
        )
        actualizar_password(s, usuario, datos)

        # 0 llamadas a la plantilla al actualizar password
        assert mock_wpp["template"].call_count == 0

        notif = s.query(Notificacion).filter(
            Notificacion.usuario_id == usuario_id,
            Notificacion.tipo == TipoNotificacion.CAMBIO_CONTRASENA,
        ).first()
        assert notif is not None
        assert notif.canal_whatsapp is True
        assert notif.enviada_whatsapp is False
        created_at = notif.created_at

    # _job_reintentar_inmediatas con ahora = creación + 3 minutos -> 0 llamadas a la plantilla
    ahora_3m = created_at + timedelta(minutes=3)
    if ahora_3m.tzinfo is None:
        ahora_3m = ahora_3m.replace(tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.notificacion_despacho_service.ahora_argentina", lambda: ahora_3m)

    _job_reintentar_inmediatas(session_factory)
    assert mock_wpp["template"].call_count == 0

    # Con ahora = creación + 16 minutos -> app.services.notificacion_despacho_service.enviar_alerta_admin con 0 llamadas
    ahora_16m = created_at + timedelta(minutes=16)
    if ahora_16m.tzinfo is None:
        ahora_16m = ahora_16m.replace(tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.notificacion_despacho_service.ahora_argentina", lambda: ahora_16m)

    _job_reintentar_inmediatas(session_factory)
    assert mock_alerta.call_count == 0


def test_13_reintento_alerta_admin_con_telefono(session_factory, mock_wpp, monkeypatch):
    """13. Usuario con teléfono y la plantilla devolviendo siempre False.
    actualizar_email -> notificación pendiente.
    _job_reintentar_inmediatas con ahora = creación + 16 minutos -> enviar_alerta_admin con 1 llamada y clave='whatsapp_seguridad_fallida'."""
    mock_wpp["template"].return_value = False
    mock_alerta = MagicMock()
    monkeypatch.setattr("app.services.notificacion_despacho_service.enviar_alerta_admin", mock_alerta)

    with session_factory() as s:
        usuario = _crear_usuario_test(s)
        usuario_id = usuario.id
        assert usuario.telefono is not None and usuario.telefono != ""

        datos = EditarEmail(email_nuevo="nuevo@example.com", password_actual="Password123!")
        actualizar_email(s, usuario, datos)

        notif = s.query(Notificacion).filter(
            Notificacion.usuario_id == usuario_id,
            Notificacion.tipo == TipoNotificacion.CAMBIO_EMAIL,
        ).first()
        assert notif is not None
        assert notif.canal_whatsapp is True
        assert notif.enviada_whatsapp is False
        created_at = notif.created_at

    # Con ahora = creación + 16 minutos -> enviar_alerta_admin con 1 llamada y clave="whatsapp_seguridad_fallida"
    ahora_16m = created_at + timedelta(minutes=16)
    if ahora_16m.tzinfo is None:
        ahora_16m = ahora_16m.replace(tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.notificacion_despacho_service.ahora_argentina", lambda: ahora_16m)

    _job_reintentar_inmediatas(session_factory)
    assert mock_alerta.call_count == 1
    _, kwargs = mock_alerta.call_args
    assert kwargs.get("clave") == "whatsapp_seguridad_fallida"


def test_14_presupuesto_plantilla_por_defecto_no_despacha(db_session, monkeypatch):
    """14. Variable WHATSAPP_PLANTILLAS_ACTIVAS por defecto.
    Usuario con teléfono y presupuesto_umbral_2_whatsapp=True.
    verificar_alertas_presupuesto con monto_usado=120 y monto_limite=100 ->
    1 notificación PRESUPUESTO_AGOTADO con canal_whatsapp=True y enviada_whatsapp=False;
    0 llamadas a app.services.presupuesto_service.enviar_whatsapp_template y 0 a app.services.presupuesto_service.enviar_whatsapp."""
    monkeypatch.setattr(
        settings,
        "WHATSAPP_PLANTILLAS_ACTIVAS",
        "resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena",
    )

    mock_presu_tpl = MagicMock(return_value=True)
    mock_presu_raw = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.presupuesto_service.enviar_whatsapp_template", mock_presu_tpl)
    monkeypatch.setattr("app.services.presupuesto_service.enviar_whatsapp", mock_presu_raw)

    usuario = _crear_usuario_test(db_session)
    cfg = db_session.query(ConfiguracionNotificacion).filter_by(usuario_id=usuario.id).first()
    cfg.presupuesto_umbral_2_whatsapp = True
    db_session.commit()

    presupuesto = SimpleNamespace(
        id=uuid.uuid4(),
        usuario_id=usuario.id,
        nombre="Comida",
        moneda=Moneda.ARS,
        categorias=[],
    )
    periodo = SimpleNamespace(
        id=uuid.uuid4(),
        monto_usado=Decimal("120"),
        monto_limite=Decimal("100"),
    )

    verificar_alertas_presupuesto(db_session, presupuesto, periodo)

    # 1 notificación PRESUPUESTO_AGOTADO con canal_whatsapp=True y enviada_whatsapp=False
    notifs = db_session.query(Notificacion).filter(
        Notificacion.usuario_id == usuario.id,
        Notificacion.tipo == TipoNotificacion.PRESUPUESTO_AGOTADO,
    ).all()
    assert len(notifs) == 1
    assert notifs[0].canal_whatsapp is True
    assert notifs[0].enviada_whatsapp is False

    # 0 llamadas a enviar_whatsapp_template y 0 a enviar_whatsapp en presupuesto_service
    assert mock_presu_tpl.call_count == 0
    assert mock_presu_raw.call_count == 0


def test_15_presupuesto_plantilla_activa_despacha(db_session, monkeypatch):
    """15. Igual al 14, con WHATSAPP_PLANTILLAS_ACTIVAS = 'resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena,alerta_presupuesto_agotado' ->
    1 llamada a la plantilla con 'alerta_presupuesto_agotado' y la notificación queda enviada_whatsapp=True."""
    monkeypatch.setattr(
        settings,
        "WHATSAPP_PLANTILLAS_ACTIVAS",
        "resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena,alerta_presupuesto_agotado",
    )

    mock_presu_tpl = MagicMock(return_value=True)
    mock_presu_raw = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.presupuesto_service.enviar_whatsapp_template", mock_presu_tpl)
    monkeypatch.setattr("app.services.presupuesto_service.enviar_whatsapp", mock_presu_raw)

    usuario = _crear_usuario_test(db_session)
    cfg = db_session.query(ConfiguracionNotificacion).filter_by(usuario_id=usuario.id).first()
    cfg.presupuesto_umbral_2_whatsapp = True
    db_session.commit()

    presupuesto = SimpleNamespace(
        id=uuid.uuid4(),
        usuario_id=usuario.id,
        nombre="Comida",
        moneda=Moneda.ARS,
        categorias=[],
    )
    periodo = SimpleNamespace(
        id=uuid.uuid4(),
        monto_usado=Decimal("120"),
        monto_limite=Decimal("100"),
    )

    verificar_alertas_presupuesto(db_session, presupuesto, periodo)

    # 1 llamada a la plantilla con 'alerta_presupuesto_agotado'
    assert mock_presu_tpl.call_count == 1
    args, _ = mock_presu_tpl.call_args
    assert args[1] == "alerta_presupuesto_agotado"

    # La notificación queda enviada_whatsapp=True
    notif = db_session.query(Notificacion).filter(
        Notificacion.usuario_id == usuario.id,
        Notificacion.tipo == TipoNotificacion.PRESUPUESTO_AGOTADO,
    ).first()
    assert notif is not None
    assert notif.enviada_whatsapp is True
