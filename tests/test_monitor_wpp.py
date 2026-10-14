from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Compiladores para SQLite en pruebas
if not hasattr(PGUUID, "_compiler_dispatcher") or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {}):
    @compiles(PGUUID, "sqlite")
    def compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if not hasattr(JSONB, "_compiler_dispatcher") or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {}):
    @compiles(JSONB, "sqlite")
    def compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"


from app.core.config import settings
from app.core.database import Base, get_db
from app.core.auth import get_current_admin_user, get_current_user
from app.main import app
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.rate_limit import RateLimit
from app.models.uso_ia import UsoIA
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.etapa_despacho import _metricas_respuesta
from app.services.uso_ia_service import purgar_uso_ia_antiguo
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


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
    monkeypatch.setattr("app.services.rate_limit_service.SessionLocal", TestingSession)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="client")
def client_fixture(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _crear_usuario(
    db: Session,
    email: str,
    es_admin: bool = False,
    permiso_lectura: bool = False,
) -> Usuario:
    ahora = datetime.now(timezone.utc)
    u = Usuario(
        id=uuid4(),
        email=email,
        telefono_normalizado="+5491100000001" if "a" in email else "+5491100000002",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.ADMIN if es_admin else RolUsuario.USUARIO,
        is_admin=es_admin,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="Monitor",
        permiso_lectura_mensajes_en=ahora - timedelta(days=1) if permiso_lectura else None,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_monitor_wpp_completo(db_session: Session, client: TestClient):
    """Prueba integral según especificación de Decisión 4, 5, 6 y D."""
    admin = _crear_usuario(db_session, "admin@argentum.com", es_admin=True)
    user_a = _crear_usuario(db_session, "user_a@argentum.com", permiso_lectura=True)
    user_b = _crear_usuario(db_session, "user_b@argentum.com", permiso_lectura=False)

    hoy = hoy_argentina()
    # Horarios de hoy en Argentina convertidos a UTC
    def ar_dt(h: int, m: int, dias_atras: int = 0) -> datetime:
        d = hoy - timedelta(days=dias_atras)
        dt_local = datetime(d.year, d.month, d.day, h, m, 0, tzinfo=TZ_ARGENTINA)
        return dt_local.astimezone(timezone.utc)

    # Conversaciones de A:
    # 10:00 registrar_transaccion (tiempo 2000)
    # 10:02 deshacer (sin tiempo)
    # 11:00 consultar_gastos (1000)
    # 12:00 desconocido, error_ia False (3000)
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_a.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="Gasté 500 en café",
        mensaje_bot="Listo, registré tu gasto",
        intent_detectado="registrar_transaccion",
        tiempo_respuesta_ms=2000,
        error_ia=False,
        fecha=ar_dt(10, 0),
    ))
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_a.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="deshacer",
        mensaje_bot="Deshecho",
        intent_detectado="deshacer",
        tiempo_respuesta_ms=None,
        error_ia=False,
        fecha=ar_dt(10, 2),
    ))
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_a.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="cuánto gasté?",
        mensaje_bot="Gastaste 500",
        intent_detectado="consultar_gastos",
        tiempo_respuesta_ms=1000,
        error_ia=False,
        fecha=ar_dt(11, 0),
    ))
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_a.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="palabra rara",
        mensaje_bot="No entendí",
        intent_detectado="desconocido",
        tiempo_respuesta_ms=3000,
        error_ia=False,
        fecha=ar_dt(12, 0),
    ))

    # Conversaciones de B:
    # 10:00 registrar_transaccion (4000)
    # 10:30 corregir (sin tiempo)
    # 13:00 desconocido, error_ia True (5000)
    # 14:00 tope_superado (sin tiempo)
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_b.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="Pagué cena 2000",
        mensaje_bot="Registrado",
        intent_detectado="registrar_transaccion",
        tiempo_respuesta_ms=4000,
        error_ia=False,
        fecha=ar_dt(10, 0),
    ))
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_b.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="corregir",
        mensaje_bot="Qué corregimos?",
        intent_detectado="corregir",
        tiempo_respuesta_ms=None,
        error_ia=False,
        fecha=ar_dt(10, 30),
    ))
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_b.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="mensaje fallo",
        mensaje_bot="Ocurrió un error",
        intent_detectado="desconocido",
        tiempo_respuesta_ms=5000,
        error_ia=True,
        fecha=ar_dt(13, 0),
    ))
    db_session.add(ConversacionWpp(
        id=uuid4(),
        usuario_id=user_b.id,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_usuario="mas mensajes",
        mensaje_bot="Superaste el tope",
        intent_detectado="tope_superado",
        tiempo_respuesta_ms=None,
        error_ia=False,
        fecha=ar_dt(14, 0),
    ))

    # Uso IA de hoy:
    # A texto_whatsapp 0.010000
    # A imagen_whatsapp 0.020000
    # B audio_whatsapp 0.003000
    # NULL importacion_pdf 0.001000
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=user_a.id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        tokens_entrada=100,
        tokens_salida=50,
        tokens_entrada_cache=0,
        costo_usd=Decimal("0.010000"),
        fecha=ar_dt(10, 1),
    ))
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=user_a.id,
        funcion="imagen_whatsapp",
        modelo="gpt-4o-mini",
        tokens_entrada=200,
        tokens_salida=50,
        tokens_entrada_cache=0,
        costo_usd=Decimal("0.020000"),
        fecha=ar_dt(10, 15),
    ))
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=user_b.id,
        funcion="audio_whatsapp",
        modelo="whisper-1",
        tokens_entrada=0,
        tokens_salida=0,
        tokens_entrada_cache=0,
        costo_usd=Decimal("0.003000"),
        fecha=ar_dt(10, 20),
    ))
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=None,
        funcion="importacion_pdf",
        modelo="gpt-4o-mini",
        tokens_entrada=10,
        tokens_salida=10,
        tokens_entrada_cache=0,
        costo_usd=Decimal("0.001000"),
        fecha=ar_dt(10, 25),
    ))
    # Ayer: A texto_whatsapp 0.500000
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=user_a.id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        tokens_entrada=5000,
        tokens_salida=500,
        tokens_entrada_cache=0,
        costo_usd=Decimal("0.500000"),
        fecha=ar_dt(15, 0, dias_atras=1),
    ))

    # Rate limits: A "mensajes_ia_dia" con cantidad 151 y expira_en mañana
    manana = datetime.now(timezone.utc) + timedelta(days=1)
    db_session.add(RateLimit(
        id=uuid4(),
        accion="mensajes_ia_dia",
        identificador=str(user_a.id),
        cantidad=151,
        ventana_segundos=86400,
        ventana_inicio=datetime.now(timezone.utc),
        expira_en=manana,
    ))

    # Notificaciones de hoy con canal_whatsapp True: 2 enviadas, 1 sin enviar
    for i in range(2):
        db_session.add(Notificacion(
            id=uuid4(),
            usuario_id=user_a.id,
            tipo=TipoNotificacion.RESUMEN_SEMANAL,
            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
            mensaje=f"Aviso {i}",
            canal_whatsapp=True,
            enviada_whatsapp=True,
            created_at=ar_dt(9, 30),
        ))
    db_session.add(Notificacion(
        id=uuid4(),
        usuario_id=user_b.id,
        tipo=TipoNotificacion.RESUMEN_SEMANAL,
        nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
        mensaje="Aviso pendiente",
        canal_whatsapp=True,
        enviada_whatsapp=False,
        created_at=ar_dt(9, 35),
    ))

    db_session.commit()

    # Autenticar como admin
    app.dependency_overrides[get_current_admin_user] = lambda: admin
    app.dependency_overrides[get_current_user] = lambda: admin

    # 1. GET /v1/admin/monitor-wpp/resumen?dias=1
    res_1 = client.get("/v1/admin/monitor-wpp/resumen?dias=1")
    assert res_1.status_code == status.HTTP_200_OK
    data_1 = res_1.json()

    calidad = data_1["calidad"]
    assert calidad["total"] == 7
    assert calidad["entendidos_primera"] == 2
    assert float(calidad["porcentaje_entendidos_primera"]) == 40.0
    assert calidad["no_entendidos"] == 1
    assert calidad["deshechos_o_corregidos"] == 1
    assert calidad["correcciones"] == 2
    assert calidad["errores_ia"] == 1
    assert calidad["topes"] == 1

    costo = data_1["costo"]
    assert float(costo["total_usd"]) == 0.034
    por_func = costo["por_funcion"]
    assert float(por_func["texto_whatsapp"]) == 0.010
    assert float(por_func["imagen_whatsapp"]) == 0.020
    assert float(por_func["audio_whatsapp"]) == 0.003
    assert float(por_func["importacion_pdf"]) == 0.001
    assert float(por_func["pdf_whatsapp"]) == 0.0
    assert float(costo["sin_usuario_usd"]) == 0.001

    por_user = costo["por_usuario"]
    assert len(por_user) == 2
    assert por_user[0]["usuario_id"] == str(user_a.id)
    assert float(por_user[0]["total_usd"]) == 0.030
    assert por_user[1]["usuario_id"] == str(user_b.id)
    assert float(por_user[1]["total_usd"]) == 0.003

    topes_hoy = data_1["topes_hoy"]
    assert float(topes_hoy["gasto_hoy_usd"]) == 0.034
    assert float(topes_hoy["presupuesto_usd"]) == float(settings.IA_PRESUPUESTO_DIARIO_USD)
    assert topes_hoy["nivel"] == "normal"
    assert topes_hoy["usuarios_tope_mensajes"] == 1
    assert topes_hoy["usuarios_tope_archivos"] == 0

    salud = data_1["salud"]
    assert salud["tiempo_respuesta_mediana_ms"] == 3000
    assert salud["tiempo_respuesta_p90_ms"] == 4600
    assert salud["mensajes_con_tiempo"] == 5
    assert salud["avisos_whatsapp_enviados"] == 2
    assert salud["avisos_whatsapp_sin_enviar"] == 1

    # 2. GET /v1/admin/monitor-wpp/resumen?dias=7
    res_7 = client.get("/v1/admin/monitor-wpp/resumen?dias=7")
    assert res_7.status_code == status.HTTP_200_OK
    data_7 = res_7.json()
    assert float(data_7["costo"]["total_usd"]) == 0.534

    # 3. GET /v1/admin/monitor-wpp/conversaciones?dias=1
    res_conv = client.get("/v1/admin/monitor-wpp/conversaciones?dias=1")
    assert res_conv.status_code == status.HTTP_200_OK
    conv_data = res_conv.json()
    assert conv_data["total"] == 8

    # Verificar visibilidad de texto según permiso
    for item in conv_data["items"]:
        if item["usuario_id"] == str(user_b.id):
            assert item["texto_visible"] is False
            assert item["mensaje_usuario"] is None
            assert item["transcripcion"] is None
            assert item["mensaje_bot"] is None
        elif item["usuario_id"] == str(user_a.id):
            assert item["texto_visible"] is True
            assert item["mensaje_usuario"] is not None
            assert item["mensaje_bot"] is not None

    # Filtro categoria=correccion -> 2
    res_corr = client.get("/v1/admin/monitor-wpp/conversaciones?dias=1&categoria=correccion")
    assert res_corr.status_code == status.HTTP_200_OK
    assert res_corr.json()["total"] == 2
    for item in res_corr.json()["items"]:
        assert item["categoria"] == "correccion"

    # 4. PUT /usuarios/me/permiso-mensajes y visibilidad de conversaciones
    # Override con user_a
    app.dependency_overrides[get_current_user] = lambda: user_a

    put_false = client.put("/usuarios/me/permiso-mensajes", json={"permitido": False})
    assert put_false.status_code == status.HTTP_200_OK
    assert put_false.json()["permiso_lectura_mensajes_en"] is None

    # GET /usuarios/me
    get_me = client.get("/usuarios/me")
    assert get_me.status_code == status.HTTP_200_OK
    assert get_me.json()["permiso_lectura_mensajes_en"] is None

    # Ahora verificar que las conversaciones de A en el monitor están ocultas
    app.dependency_overrides[get_current_admin_user] = lambda: admin
    res_conv_despues = client.get("/v1/admin/monitor-wpp/conversaciones?dias=1")
    for item in res_conv_despues.json()["items"]:
        if item["usuario_id"] == str(user_a.id):
            assert item["texto_visible"] is False
            assert item["mensaje_usuario"] is None
            assert item["mensaje_bot"] is None

    # PUT permiso true
    app.dependency_overrides[get_current_user] = lambda: user_a
    put_true = client.put("/usuarios/me/permiso-mensajes", json={"permitido": True})
    assert put_true.status_code == status.HTTP_200_OK
    assert put_true.json()["permiso_lectura_mensajes_en"] is not None

    get_me_true = client.get("/usuarios/me")
    assert get_me_true.status_code == status.HTTP_200_OK
    assert get_me_true.json()["permiso_lectura_mensajes_en"] is not None

    # 5. Validación de dias=5 -> 422
    app.dependency_overrides[get_current_admin_user] = lambda: admin
    res_422_res = client.get("/v1/admin/monitor-wpp/resumen?dias=5")
    assert res_422_res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    res_422_conv = client.get("/v1/admin/monitor-wpp/conversaciones?dias=5")
    assert res_422_conv.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 6. Usuario no admin -> 403 en ambos endpoints
    def _override_admin_check():
        from fastapi import HTTPException
        if not user_b.is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenés permisos de administrador.")
        return user_b

    app.dependency_overrides[get_current_admin_user] = _override_admin_check
    res_403_res = client.get("/v1/admin/monitor-wpp/resumen?dias=1")
    assert res_403_res.status_code == status.HTTP_403_FORBIDDEN

    res_403_conv = client.get("/v1/admin/monitor-wpp/conversaciones?dias=1")
    assert res_403_conv.status_code == status.HTTP_403_FORBIDDEN


def test_metricas_respuesta():
    """Prueba unitaria de _metricas_respuesta según Decisión 3 y D."""
    ctx = MagicMock()
    ctx.t_inicio = 100.0

    with patch("time.perf_counter", return_value=101.25):
        tiempo, error = _metricas_respuesta(ctx, {})
        assert tiempo == 1250
        assert error is False

        tiempo_err, error_err = _metricas_respuesta(ctx, {"error": True})
        assert tiempo_err == 1250
        assert error_err is True


def test_purgar_uso_ia_antiguo(db_session: Session):
    """Prueba de purgar_uso_ia_antiguo según Decisión 7 y D."""
    ahora = datetime.now(timezone.utc)
    fila_vieja = UsoIA(
        id=uuid4(),
        usuario_id=None,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        tokens_entrada=10,
        tokens_salida=10,
        tokens_entrada_cache=0,
        costo_usd=Decimal("0.000100"),
        fecha=ahora - timedelta(days=401),
    )
    fila_reciente = UsoIA(
        id=uuid4(),
        usuario_id=None,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        tokens_entrada=10,
        tokens_salida=10,
        tokens_entrada_cache=0,
        costo_usd=Decimal("0.000100"),
        fecha=ahora - timedelta(days=399),
    )
    db_session.add(fila_vieja)
    db_session.add(fila_reciente)
    db_session.commit()

    id_viejo = fila_vieja.id
    id_reciente = fila_reciente.id

    borrados = purgar_uso_ia_antiguo(db_session)
    assert borrados >= 1

    assert db_session.query(UsoIA).filter_by(id=id_viejo).first() is None
    assert db_session.query(UsoIA).filter_by(id=id_reciente).first() is not None
