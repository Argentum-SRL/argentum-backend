# -*- coding: utf-8 -*-
"""
tests/test_costos_mes.py — Pruebas unitarias y de integración para el control de topes mensuales (OpenAI y Meta).
Valores exactos especificados en el prompt de la fase_j1_costos_jobs.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
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

from app.core.config import settings
from app.core.database import Base
from app.core.jobs_catalogo import JOBS_CATALOGO
from app.core.registro_jobs import vigilar_tareas
from app.models.conversacion_wpp import ConversacionWpp
from app.models.ejecucion_job import EjecucionJob
from app.models.mensaje_saliente_wpp import MensajeSalienteWpp
from app.models.uso_ia import UsoIA
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.etapa_entrada import procesar_entrada_medios_y_texto
from app.routers.whatsapp.etapa_ia import procesar_llamada_ia_y_normalizacion
from app.services.costos_service import (
    avisar_costo_si_corresponde,
    cobrados_meta_mes,
    exento_tope_mensual,
    gasto_ia_mes_usd,
    gasto_meta_mes_ars,
    inicio_mes_ar,
    nivel_alcanzado,
    texto_aviso_costo,
    vigilar_costos,
)
from app.services.uso_ia_service import TEXTO_MES_SIN_IA, verificar_cupo_ia
from app.utils.fecha import TZ_ARGENTINA


@pytest.fixture(name="db_session")
def db_session_fixture(monkeypatch):
    # Soporte SQLite para comparaciones aware/naive en synchronize_session del evaluador
    from sqlalchemy.orm.evaluator import _EvaluatorCompiler
    orig_straight_evaluate = _EvaluatorCompiler._straight_evaluate

    def _safe_straight_evaluate(self, operator, eval_left, eval_right, clause):
        orig_eval = orig_straight_evaluate(self, operator, eval_left, eval_right, clause)
        def evaluate(obj):
            try:
                return orig_eval(obj)
            except TypeError as e:
                if "offset-naive and offset-aware" in str(e):
                    left_val = eval_left(obj)
                    right_val = eval_right(obj)
                    if isinstance(left_val, datetime) and isinstance(right_val, datetime):
                        if left_val.tzinfo is None and right_val.tzinfo is not None:
                            left_val = left_val.replace(tzinfo=timezone.utc)
                        elif right_val.tzinfo is None and left_val.tzinfo is not None:
                            right_val = right_val.replace(tzinfo=timezone.utc)
                        return operator(left_val, right_val)
                raise
        return evaluate

    monkeypatch.setattr(_EvaluatorCompiler, "_straight_evaluate", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_lt_binary_op", _safe_straight_evaluate)
    monkeypatch.setattr(_EvaluatorCompiler, "visit_le_binary_op", _safe_straight_evaluate)

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
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="mock_mails")
def mock_mails_fixture(monkeypatch):
    mails = []

    def fake_enviar(dest, asunto, cuerpo, cuerpo_html=None):
        mails.append((dest, asunto, cuerpo))
        return True

    monkeypatch.setattr("app.services.costos_service._enviar_email", fake_enviar)
    monkeypatch.setattr(
        "app.services.costos_service.destinatarios_reporte",
        lambda: ["sebastian@prueba.com", "manu@prueba.com"],
    )
    return mails


def _crear_usuario(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.test",
        telefono_normalizado="+5491100001111",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="Costos",
    )
    db.add(u)
    db.commit()
    return u


def test_nivel_alcanzado_casos_exactos():
    assert nivel_alcanzado(4.99, 10) is None
    assert nivel_alcanzado(5, 10) == 50
    assert nivel_alcanzado(7.99, 10) == 50
    assert nivel_alcanzado(8, 10) == 80
    assert nivel_alcanzado(9.99, 10) == 80
    assert nivel_alcanzado(10, 10) == 100
    assert nivel_alcanzado(25, 10) == 100
    assert nivel_alcanzado(5, 0) is None
    assert nivel_alcanzado(2486.88, 5000) is None
    assert nivel_alcanzado(2524.56, 5000) == 50
    assert nivel_alcanzado(4031.76, 5000) == 80
    assert nivel_alcanzado(5011.44, 5000) == 100


def test_inicio_mes_ar():
    hoy = date(2026, 11, 6)
    assert inicio_mes_ar(hoy) == datetime(2026, 11, 1, 0, 0, tzinfo=TZ_ARGENTINA)


def test_texto_aviso_costo_openai():
    hoy = date(2026, 11, 6)
    asunto, cuerpo = texto_aviso_costo("OpenAI", 50, Decimal("5"), Decimal("10"), "USD", hoy)
    assert asunto == "[Argentum] OpenAI: se usó el 50% del tope del mes"
    assert cuerpo == (
        "Gasto estimado del mes 11/2026: US$5,00 de un tope de US$10,00.\n"
        "Monto estimado por Argentum, no facturado por OpenAI."
    )

    _, cuerpo_100 = texto_aviso_costo("OpenAI", 100, Decimal("10"), Decimal("10"), "USD", hoy)
    assert cuerpo_100 == (
        "Gasto estimado del mes 11/2026: US$10,00 de un tope de US$10,00.\n"
        "El bot de WhatsApp dejó de usar la IA hasta el 1° del mes que viene. La web sigue andando.\n"
        "Monto estimado por Argentum, no facturado por OpenAI."
    )


def test_texto_aviso_costo_meta():
    hoy = date(2026, 11, 6)
    asunto_80, cuerpo_80 = texto_aviso_costo("Meta", 80, Decimal("4031.76"), Decimal("5000"), "ARS", hoy)
    assert asunto_80 == "[Argentum] Meta: se usó el 80% del tope del mes"
    assert cuerpo_80 == (
        "Gasto estimado del mes 11/2026: $4.032 de un tope de $5.000.\n"
        "Monto estimado por Argentum, no facturado por Meta."
    )

    _, cuerpo_100 = texto_aviso_costo("Meta", 100, Decimal("5011.44"), Decimal("5000"), "ARS", hoy)
    assert cuerpo_100 == (
        "Gasto estimado del mes 11/2026: $5.011 de un tope de $5.000.\n"
        "WhatsApp sigue andando: el tope de Meta es solo un aviso.\n"
        "Monto estimado por Argentum, no facturado por Meta."
    )


def test_exento_tope_mensual_y_docstring():
    u = object()
    assert exento_tope_mensual(u) is False
    assert exento_tope_mensual(None) is False
    fuente = inspect.getsource(exento_tope_mensual)
    assert "PLAN PAGO" in fuente


def test_settings_valores_esperados():
    assert settings.IA_PRESUPUESTO_MENSUAL_USD == 10.0
    assert settings.META_TOPE_MENSUAL_ARS == Decimal("5000")
    assert settings.WHATSAPP_TARIFA_SERVICIO_ARS == Decimal("37.68")
    assert settings.IA_PRESUPUESTO_DIARIO_USD == 1.0


def test_gasto_ia_mes_usd(db_session: Session):
    hoy = date(2026, 11, 6)
    u = _crear_usuario(db_session)

    # 31/10/2026 15:00 AR 3,00
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 10, 31, 15, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("3.00"),
    ))
    # 01/11/2026 10:00 AR 2,00
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 1, 10, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("2.00"),
    ))
    # 05/11/2026 10:00 AR 1,50
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 5, 10, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("1.50"),
    ))
    db_session.commit()

    assert gasto_ia_mes_usd(db_session, hoy) == Decimal("3.50")
    assert gasto_ia_mes_usd(db_session, date(2026, 12, 1)) == Decimal("0")


def test_cobrados_meta_mes_y_gasto_meta(db_session: Session):
    hoy = date(2026, 11, 6)
    u = _crear_usuario(db_session)

    # 3 enviadas "regular" en noviembre
    for _ in range(3):
        db_session.add(MensajeSalienteWpp(
            id=uuid4(),
            usuario_id=u.id,
            fecha=datetime(2026, 11, 2, 12, 0, tzinfo=TZ_ARGENTINA),
            tipo="texto",
            enviado=True,
            tipo_cobro="regular",
        ))
    # 1 enviada "free_customer_service" en noviembre
    db_session.add(MensajeSalienteWpp(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 3, 12, 0, tzinfo=TZ_ARGENTINA),
        tipo="texto",
        enviado=True,
        tipo_cobro="free_customer_service",
    ))
    # 1 enviada sin tipo_cobro en noviembre
    db_session.add(MensajeSalienteWpp(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 4, 12, 0, tzinfo=TZ_ARGENTINA),
        tipo="texto",
        enviado=True,
        tipo_cobro=None,
    ))
    # 1 NO enviada con "regular" en noviembre
    db_session.add(MensajeSalienteWpp(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 5, 12, 0, tzinfo=TZ_ARGENTINA),
        tipo="texto",
        enviado=False,
        tipo_cobro="regular",
    ))
    # 1 enviada "regular" el 31/10/2026 15:00 AR
    db_session.add(MensajeSalienteWpp(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 10, 31, 15, 0, tzinfo=TZ_ARGENTINA),
        tipo="texto",
        enviado=True,
        tipo_cobro="regular",
    ))
    db_session.commit()

    assert cobrados_meta_mes(db_session, hoy) == 3
    assert gasto_meta_mes_ars(db_session, hoy) == Decimal("113.04")


def test_avisar_costo_si_corresponde_completo(db_session: Session, mock_mails, monkeypatch):
    hoy = date(2026, 11, 6)

    # 1. OpenAI con 5 y tope 10 -> nivel 50 y 2 mails
    niv = avisar_costo_si_corresponde("OpenAI", Decimal("5"), Decimal("10"), "USD", hoy)
    assert niv == 50
    assert len(mock_mails) == 2
    assert mock_mails[0][0] == "sebastian@prueba.com"
    assert mock_mails[1][0] == "manu@prueba.com"
    assert mock_mails[0][1] == "[Argentum] OpenAI: se usó el 50% del tope del mes"

    # 2. Otra vez con 5 -> None y 0 mails nuevos (ya se avisó este mes en nivel 50)
    assert avisar_costo_si_corresponde("OpenAI", Decimal("5"), Decimal("10"), "USD", hoy) is None
    assert len(mock_mails) == 2

    # 3. Con 8 -> 80 y 2 mails nuevos
    niv_80 = avisar_costo_si_corresponde("OpenAI", Decimal("8"), Decimal("10"), "USD", hoy)
    assert niv_80 == 80
    assert len(mock_mails) == 4
    assert mock_mails[-1][1] == "[Argentum] OpenAI: se usó el 80% del tope del mes"

    # 4. Con 4.99 en base vacía -> None y 0 mails nuevos
    assert avisar_costo_si_corresponde("OpenAI", Decimal("4.99"), Decimal("10"), "USD", hoy) is None
    assert len(mock_mails) == 4

    # 5. Con 5 y hoy 01/12/2026 -> 50 (mes nuevo) y 2 mails nuevos
    hoy_dic = date(2026, 12, 1)
    niv_dic = avisar_costo_si_corresponde("OpenAI", Decimal("5"), Decimal("10"), "USD", hoy_dic)
    assert niv_dic == 50
    assert len(mock_mails) == 6

    # 6. "Meta" con 2524.56 y tope 5000 el 06/11/2026 -> 50 y el cuerpo empieza con "Gasto estimado del mes 11/2026: $2.525 de un tope de $5.000."
    niv_meta = avisar_costo_si_corresponde("Meta", Decimal("2524.56"), Decimal("5000"), "ARS", hoy)
    assert niv_meta == 50
    assert len(mock_mails) == 8
    assert mock_mails[-1][2].startswith("Gasto estimado del mes 11/2026: $2.525 de un tope de $5.000.")

    # 7. Con destinatarios_reporte devolviendo [] -> devuelve el nivel y 0 mails nuevos
    monkeypatch.setattr("app.services.costos_service.destinatarios_reporte", lambda: [])
    niv_meta_vacio = avisar_costo_si_corresponde("Meta", Decimal("4031.76"), Decimal("5000"), "ARS", hoy)
    assert niv_meta_vacio == 80
    assert len(mock_mails) == 8

    # 8. Con verificar_rate_limit parcheado para lanzar -> None, sin propagar
    def fake_rate_limit_falla(*a, **kw):
        raise RuntimeError("Falla rate limit simulada")

    monkeypatch.setattr("app.services.costos_service.verificar_rate_limit", fake_rate_limit_falla)
    assert avisar_costo_si_corresponde("OpenAI", Decimal("10"), Decimal("10"), "USD", hoy) is None


def test_verificar_cupo_ia_tope_mensual(db_session: Session, mock_mails, monkeypatch):
    hoy = date(2026, 11, 6)
    monkeypatch.setattr("app.services.uso_ia_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr(settings, "IA_PRESUPUESTO_MENSUAL_USD", 10.0)
    monkeypatch.setattr(settings, "IA_PRESUPUESTO_DIARIO_USD", 100.0)

    u = _crear_usuario(db_session)

    # Filas de uso_ia del mes: 02/11/2026 10:00 AR con 9.99
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 2, 10, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("9.99"),
    ))
    # Gasto del mes anterior (31/10/2026 15:00 AR, 50.00) no cuenta
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 10, 31, 15, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("50.00"),
    ))
    db_session.commit()

    # Track commit en la sesión recibida
    commit_llamado = False
    orig_commit = db_session.commit

    def tracked_commit():
        nonlocal commit_llamado
        commit_llamado = True
        return orig_commit()

    db_session.commit = tracked_commit

    alertas_admin = []
    monkeypatch.setattr(
        "app.services.uso_ia_service.enviar_alerta_admin",
        lambda *a, **kw: alertas_admin.append(kw),
    )

    # Con 9.99 -> (True, None) para "mensaje", y salió mail de nivel 80 a cada destinatario
    permitido, texto = verificar_cupo_ia(db_session, u, "mensaje")
    assert permitido is True
    assert texto is None
    assert len(mock_mails) == 2
    assert mock_mails[0][1] == "[Argentum] OpenAI: se usó el 80% del tope del mes"

    # Con 10.00 (agregando 0.01)
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 2, 10, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("0.01"),
    ))
    db_session.commit()
    commit_llamado = False  # Reset

    # Para "mensaje", "archivo" y "audio" devuelve (False, TEXTO_MES_SIN_IA)
    for tipo in ("mensaje", "archivo", "audio"):
        p, t = verificar_cupo_ia(db_session, u, tipo)
        assert p is False
        assert t == TEXTO_MES_SIN_IA

    # Salieron los 2 mails del 100% con el cuerpo de OpenAI 100
    assert len(mock_mails) == 4
    assert mock_mails[2][1] == "[Argentum] OpenAI: se usó el 100% del tope del mes"
    assert "El bot de WhatsApp dejó de usar la IA hasta el 1° del mes que viene. La web sigue andando." in mock_mails[2][2]

    # Con usuario None -> el mismo (False, texto)
    p_none, t_none = verificar_cupo_ia(db_session, None, "mensaje")
    assert p_none is False
    assert t_none == TEXTO_MES_SIN_IA

    # Con exento_tope_mensual parcheado en app.services.uso_ia_service para devolver True -> (True, None)
    monkeypatch.setattr("app.services.uso_ia_service.exento_tope_mensual", lambda user: True)
    p_exento, t_exento = verificar_cupo_ia(db_session, u, "mensaje")
    assert p_exento is True
    assert t_exento is None

    # enviar_alerta_admin no se llamó en todo el test (el diario no se tocó)
    assert len(alertas_admin) == 0

    # La sesión recibida no recibe commit durante verificar_cupo_ia
    assert commit_llamado is False


def test_bot_texto_tope_mes_etapa_ia(db_session: Session, mock_mails, monkeypatch):
    hoy = date(2026, 11, 6)
    monkeypatch.setattr("app.services.uso_ia_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr(settings, "IA_PRESUPUESTO_MENSUAL_USD", 10.0)

    u = _crear_usuario(db_session)
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 2, 10, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("10.00"),
    ))
    db_session.commit()

    wamid = f"wamid_tope_mes_texto_{uuid4().hex[:6]}"
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={"text": {"body": "gasté 1500 en almuerzo"}},
        wamid=wamid,
        from_number=u.telefono_normalizado,
        msg_type="text",
        t_inicio=0.0,
        db=db_session,
        usuario=u,
        mensaje_texto="gasté 1500 en almuerzo",
    )

    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_enviar, \
         patch("app.services.ai_service.procesar_mensaje") as mock_ai, \
         patch("app.routers.whatsapp.media._transcribir_audio") as mock_transcribir:

        procesar_llamada_ia_y_normalizacion(ctx)

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[0][1] == TEXTO_MES_SIN_IA
    mock_ai.assert_not_called()
    mock_transcribir.assert_not_called()
    assert ctx.terminado is True

    conv = db_session.scalar(select(ConversacionWpp).where(ConversacionWpp.wamid == wamid))
    assert conv is not None
    assert conv.intent_detectado == "tope_superado"
    assert conv.mensaje_bot == TEXTO_MES_SIN_IA


def test_bot_audio_tope_mes_etapa_entrada(db_session: Session, mock_mails, monkeypatch):
    hoy = date(2026, 11, 6)
    monkeypatch.setattr("app.services.uso_ia_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr(settings, "IA_PRESUPUESTO_MENSUAL_USD", 10.0)

    u = _crear_usuario(db_session)
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 2, 10, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("10.00"),
    ))
    db_session.commit()

    wamid = f"wamid_tope_mes_audio_{uuid4().hex[:6]}"
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={"audio": {"id": "audio_media_123", "mime_type": "audio/ogg"}},
        wamid=wamid,
        from_number=u.telefono_normalizado,
        msg_type="audio",
        t_inicio=0.0,
        db=db_session,
        usuario=u,
    )

    with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_enviar, \
         patch("app.services.ai_service.procesar_mensaje") as mock_ai, \
         patch("app.routers.whatsapp.etapa_entrada._transcribir_audio") as mock_transcribir:

        procesar_entrada_medios_y_texto(ctx)

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[0][1] == TEXTO_MES_SIN_IA
    mock_ai.assert_not_called()
    mock_transcribir.assert_not_called()
    assert ctx.terminado is True


def test_vigilar_costos_y_vigilar_tareas(db_session: Session, mock_mails, monkeypatch):
    hoy = date(2026, 11, 6)
    u = _crear_usuario(db_session)

    # uso_ia de noviembre 5.00
    db_session.add(UsoIA(
        id=uuid4(),
        usuario_id=u.id,
        fecha=datetime(2026, 11, 3, 10, 0, tzinfo=TZ_ARGENTINA),
        funcion="texto",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("5.00"),
    ))
    # 67 salientes "regular" enviados en noviembre
    for _ in range(67):
        db_session.add(MensajeSalienteWpp(
            id=uuid4(),
            usuario_id=u.id,
            fecha=datetime(2026, 11, 3, 12, 0, tzinfo=TZ_ARGENTINA),
            tipo="texto",
            enviado=True,
            tipo_cobro="regular",
        ))
    db_session.commit()

    # vigilar_costos con hoy 06/11/2026 -> 4 mails (OpenAI 50 y Meta 50 a los 2 destinatarios)
    vigilar_costos(db_session, hoy)
    assert len(mock_mails) == 4
    asuntos = {m[1] for m in mock_mails}
    assert "[Argentum] OpenAI: se usó el 50% del tope del mes" in asuntos
    assert "[Argentum] Meta: se usó el 50% del tope del mes" in asuntos

    # Segunda llamada -> 0 nuevos
    vigilar_costos(db_session, hoy)
    assert len(mock_mails) == 4

    # vigilar_tareas con fecha hoy parcheada
    llamadas_vigilar = []

    def mock_vigilar(db, fecha_hoy):
        llamadas_vigilar.append(fecha_hoy)

    monkeypatch.setattr("app.services.costos_service.vigilar_costos", mock_vigilar)
    monkeypatch.setattr("app.core.registro_jobs.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.core.registro_jobs.enviar_alerta_admin", lambda *a, **kw: True)

    vigilar_tareas(db=db_session)
    assert len(llamadas_vigilar) == 1
    assert llamadas_vigilar[0] == hoy

    # vigilar_costos parcheado para lanzar RuntimeError -> vigilar_tareas no lanza y la purga de más de 14 días ocurrió
    def mock_vigilar_falla(db, fecha_hoy):
        raise RuntimeError("Falla vigilando costos simulada")

    monkeypatch.setattr("app.services.costos_service.vigilar_costos", mock_vigilar_falla)

    # Insertar ejecución antigua para purga (> 14 días)
    vieja_ejec = EjecucionJob(
        job_id="test_old",
        inicio=datetime.now(timezone.utc) - timedelta(days=20),
        fin=datetime.now(timezone.utc) - timedelta(days=20) + timedelta(seconds=1),
        resultado="ok",
        duracion_ms=1000,
    )
    db_session.add(vieja_ejec)
    db_session.commit()
    vieja_id = vieja_ejec.id

    vigilar_tareas(db=db_session)  # No lanza

    purga_check = db_session.scalar(select(EjecucionJob).where(EjecucionJob.id == vieja_id))
    assert purga_check is None


def test_jobs_catalogo_vigilar_tareas():
    item = next((j for j in JOBS_CATALOGO if j.job_id == "vigilar_tareas"), None)
    assert item is not None
    assert item.que_hace == (
        "Revisa el estado de todas las tareas y alerta por email si alguna falla o se atrasa; "
        "además avisa cuando el gasto del mes en OpenAI o en WhatsApp llega al 50%, 80% o 100% del tope."
    )
    assert len(JOBS_CATALOGO) == 24
