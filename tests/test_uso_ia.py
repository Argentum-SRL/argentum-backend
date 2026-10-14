from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Compiladores para SQLite en fixtures de prueba
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
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.uso_ia import UsoIA
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.etapa_entrada import procesar_entrada_medios_y_texto
from app.routers.whatsapp.etapa_ia import procesar_llamada_ia_y_normalizacion
from app.services.uso_ia_service import (
    TEXTO_GLOBAL_SIN_ARCHIVOS,
    TEXTO_GLOBAL_SIN_IA,
    TEXTO_TOPE_ARCHIVOS_USUARIO,
    TEXTO_TOPE_MENSAJES_USUARIO,
    calcular_costo_ia,
    registrar_uso_ia,
    verificar_cupo_ia,
)
from app.services.usuario_service import eliminar_usuario
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


def _crear_usuario_test(db: Session) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"test_{uuid4().hex[:6]}@argentum.com",
        telefono_normalizado="+5491100001111",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="IA",
    )
    db.add(u)
    db.commit()
    return u


def test_costo_gpt_4o_mini():
    """gpt-4o-mini-2024-07-18 con 10.000 tokens de entrada (8.000 en caché) y 200 de salida: costo 0.00102."""
    costo = calcular_costo_ia(
        modelo="gpt-4o-mini-2024-07-18",
        tokens_entrada=10000,
        tokens_entrada_cache=8000,
        tokens_salida=200,
    )
    assert float(costo) == 0.00102


def test_costo_gpt_4o():
    """gpt-4o con 1.500 de entrada, 0 en caché y 300 de salida: costo 0.00675."""
    costo = calcular_costo_ia(
        modelo="gpt-4o",
        tokens_entrada=1500,
        tokens_entrada_cache=0,
        tokens_salida=300,
    )
    assert float(costo) == 0.00675


def test_costo_whisper():
    """whisper-1 con 30 segundos: costo 0.003."""
    costo = calcular_costo_ia(
        modelo="whisper-1",
        segundos_audio=30,
    )
    assert float(costo) == 0.003


def test_costo_modelo_desconocido():
    """Modelo 'modelo-raro' con 1.000.000 de entrada y 0 de salida: costo 2.5."""
    costo = calcular_costo_ia(
        modelo="modelo-raro",
        tokens_entrada=1000000,
        tokens_entrada_cache=0,
        tokens_salida=0,
    )
    assert float(costo) == 2.5


def test_registrar_uso_ia_base_rota_no_lanza(monkeypatch):
    """registrar_uso_ia con la base rota (SessionLocal que lanza excepción) no lanza."""
    def _raising_session():
        raise RuntimeError("Conexión rota simulada a base de datos")

    monkeypatch.setattr("app.services.uso_ia_service.SessionLocal", _raising_session)

    # Debe capturar y no lanzar excepción
    registrar_uso_ia(
        usuario_id=uuid4(),
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        respuesta={"usage": {"prompt_tokens": 100, "completion_tokens": 20}},
    )


def test_tope_mensajes_usuario(db_session: Session):
    """Usuario con 150 mensajes en la ventana: el 151 da (False, texto exacto de tope de mensajes); el 150 da (True, None)."""
    usuario = _crear_usuario_test(db_session)

    for i in range(1, 151):
        permitido, texto = verificar_cupo_ia(db_session, usuario, "mensaje")
        if i == 150:
            assert permitido is True
            assert texto is None

    permitido_151, texto_151 = verificar_cupo_ia(db_session, usuario, "mensaje")
    assert permitido_151 is False
    assert texto_151 == TEXTO_TOPE_MENSAJES_USUARIO


def test_tope_archivos_usuario(db_session: Session):
    """Usuario con 20 archivos: el 21 da (False, texto exacto de tope de archivos)."""
    usuario = _crear_usuario_test(db_session)

    for i in range(1, 21):
        permitido, texto = verificar_cupo_ia(db_session, usuario, "archivo")
        if i == 20:
            assert permitido is True
            assert texto is None

    permitido_21, texto_21 = verificar_cupo_ia(db_session, usuario, "archivo")
    assert permitido_21 is False
    assert texto_21 == TEXTO_TOPE_ARCHIVOS_USUARIO


def test_presupuesto_global_y_alertas_admin(db_session: Session, monkeypatch):
    """
    Gasto del día 0,49 con presupuesto 1: archivo permitido y sin mail.
    0,50: permitido y un mail "aviso".
    1,00: archivo bloqueado con el texto de "sin_archivos", mensaje permitido.
    2,00: mensaje bloqueado con el texto de "sin_ia".
    Llamar dos veces al mismo nivel el mismo día manda un solo mail.
    """
    usuario = _crear_usuario_test(db_session)
    mails_enviados = []

    def mock_enviar_alerta(asunto: str, cuerpo: str, clave: str | None = None) -> bool:
        mails_enviados.append((asunto, cuerpo))
        return True

    monkeypatch.setattr("app.services.uso_ia_service.enviar_alerta_admin", mock_enviar_alerta)
    monkeypatch.setattr(settings, "IA_PRESUPUESTO_DIARIO_USD", 1.0)

    hoy = hoy_argentina()
    fecha_hoy = datetime(hoy.year, hoy.month, hoy.day, 10, 0, 0, tzinfo=TZ_ARGENTINA)

    # 1. Gasto 0.49
    fila_049 = UsoIA(
        id=uuid4(),
        fecha=fecha_hoy,
        usuario_id=usuario.id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("0.490000"),
    )
    db_session.add(fila_049)
    db_session.commit()

    permitido, texto = verificar_cupo_ia(db_session, usuario, "archivo")
    assert permitido is True
    assert texto is None
    assert len(mails_enviados) == 0

    # 2. Gasto llega a 0.50 (agregamos 0.01)
    fila_001 = UsoIA(
        id=uuid4(),
        fecha=fecha_hoy,
        usuario_id=usuario.id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("0.010000"),
    )
    db_session.add(fila_001)
    db_session.commit()

    permitido, texto = verificar_cupo_ia(db_session, usuario, "archivo")
    assert permitido is True
    assert texto is None
    assert len(mails_enviados) == 1
    assert mails_enviados[-1][0] == "Argentum: la IA ya gastó la mitad del presupuesto de hoy"
    assert "Monto estimado por Argentum, no facturado por OpenAI" in mails_enviados[-1][1]

    # Segunda llamada al mismo nivel (0.50): no manda otro mail
    verificar_cupo_ia(db_session, usuario, "archivo")
    assert len(mails_enviados) == 1

    # 3. Gasto llega a 1.00 (agregamos 0.50)
    fila_050 = UsoIA(
        id=uuid4(),
        fecha=fecha_hoy,
        usuario_id=usuario.id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("0.500000"),
    )
    db_session.add(fila_050)
    db_session.commit()

    permitido_arch, texto_arch = verificar_cupo_ia(db_session, usuario, "archivo")
    assert permitido_arch is False
    assert texto_arch == TEXTO_GLOBAL_SIN_ARCHIVOS
    assert len(mails_enviados) == 2
    assert mails_enviados[-1][0] == "Argentum: se pausaron fotos y PDF por el presupuesto de la IA"

    # Mensaje sigue permitido en nivel sin_archivos
    permitido_msg, texto_msg = verificar_cupo_ia(db_session, usuario, "mensaje")
    assert permitido_msg is True
    assert texto_msg is None

    # Segunda llamada en nivel sin_archivos no manda otro mail
    verificar_cupo_ia(db_session, usuario, "archivo")
    assert len(mails_enviados) == 2

    # 4. Gasto llega a 2.00 (agregamos 1.00)
    fila_100 = UsoIA(
        id=uuid4(),
        fecha=fecha_hoy,
        usuario_id=usuario.id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("1.000000"),
    )
    db_session.add(fila_100)
    db_session.commit()

    permitido_sin_ia, texto_sin_ia = verificar_cupo_ia(db_session, usuario, "mensaje")
    assert permitido_sin_ia is False
    assert texto_sin_ia == TEXTO_GLOBAL_SIN_IA
    assert len(mails_enviados) == 3
    assert mails_enviados[-1][0] == "Argentum: se pausó la IA por el presupuesto del día"

    # Segunda llamada en sin_ia no manda otro mail
    verificar_cupo_ia(db_session, usuario, "mensaje")
    assert len(mails_enviados) == 3


def test_gasto_ayer_no_suma_a_hoy(db_session: Session):
    """Un movimiento de uso_ia de ayer no suma al gasto de hoy."""
    usuario = _crear_usuario_test(db_session)

    # Insertar gasto de ayer (hace 26 horas)
    hoy = hoy_argentina()
    ayer = datetime(hoy.year, hoy.month, hoy.day, 12, 0, 0, tzinfo=TZ_ARGENTINA) - timedelta(days=1)

    fila_ayer = UsoIA(
        id=uuid4(),
        fecha=ayer,
        usuario_id=usuario.id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        costo_usd=Decimal("5.000000"),  # Muy por encima del presupuesto
    )
    db_session.add(fila_ayer)
    db_session.commit()

    permitido, texto = verificar_cupo_ia(db_session, usuario, "mensaje")
    assert permitido is True
    assert texto is None


def test_eliminar_usuario_deja_uso_ia_con_usuario_id_null(db_session: Session):
    """eliminar_usuario deja la fila de uso_ia con usuario_id NULL y no falla."""
    usuario = _crear_usuario_test(db_session)
    user_id = usuario.id

    fila = UsoIA(
        id=uuid4(),
        fecha=datetime.now(timezone.utc),
        usuario_id=user_id,
        funcion="texto_whatsapp",
        modelo="gpt-4o-mini",
        tokens_entrada=500,
        tokens_salida=100,
        costo_usd=Decimal("0.000100"),
    )
    db_session.add(fila)
    db_session.commit()

    res = eliminar_usuario(db_session, usuario)
    assert "confirmacion" in res

    db_session.expire_all()
    fila_guardada = db_session.scalar(select(UsoIA).where(UsoIA.id == fila.id))
    assert fila_guardada is not None
    assert fila_guardada.usuario_id is None
    assert fila_guardada.costo_usd == Decimal("0.000100")


def test_aviso_rafaga_texto_exacto():
    """El aviso de ráfaga manda exactamente 'Estás enviando muchos mensajes seguidos. Por favor, esperá un momento antes de volver a escribir.'"""
    from app.routers import whatsapp_ia

    mensajes_enviados = []
    def mock_enviar(telefono, texto):
        mensajes_enviados.append((telefono, texto))

    mock_usuario = MagicMock()
    mock_usuario.id = uuid4()
    mock_usuario.telefono_normalizado = "+5491100001111"
    mock_usuario.telefono = "+5491100001111"

    datos_mensaje = {
        "msg": {"text": {"body": "hola"}},
        "wamid": "test_wamid_123",
        "from_number": "+5491100001111",
        "msg_type": "text",
        "t_inicio": 0.0,
    }

    motivo_esperado = "Estás enviando muchos mensajes seguidos. Por favor, esperá un momento antes de volver a escribir."

    with patch.object(whatsapp_ia, "SessionLocal", MagicMock()), \
         patch.object(whatsapp_ia, "marcar_leido_y_escribiendo", MagicMock()), \
         patch.object(whatsapp_ia, "_buscar_usuario_por_telefono", return_value=mock_usuario), \
         patch.object(whatsapp_ia, "_verificar_rate_limit_registrado", return_value=(False, motivo_esperado)), \
         patch("app.services.whatsapp_service.enviar_whatsapp", side_effect=mock_enviar):

        whatsapp_ia._procesar_mensaje_whatsapp_background(datos_mensaje)

    assert len(mensajes_enviados) == 1
    assert mensajes_enviados[0][1] == "Estás enviando muchos mensajes seguidos. Por favor, esperá un momento antes de volver a escribir."


def test_verificar_cupo_ia_excepcion_rate_limit_retorna_permitido(db_session: Session, monkeypatch):
    """Con verificar_rate_limit que lanza una excepción, verificar_cupo_ia devuelve (True, None)."""
    usuario = _crear_usuario_test(db_session)

    def _falla_rate_limit(*args, **kwargs):
        raise RuntimeError("Fallo inesperado simulado en verificar_rate_limit")

    monkeypatch.setattr("app.services.uso_ia_service.verificar_rate_limit", _falla_rate_limit)

    permitido, texto = verificar_cupo_ia(db_session, usuario, "mensaje")
    assert permitido is True
    assert texto is None


def test_verificar_cupo_ia_no_llama_commit_sesion_recibida(db_session: Session):
    """verificar_cupo_ia con tipo 'mensaje' y 'archivo' no llama nunca a commit de la sesión que recibe."""
    usuario = _crear_usuario_test(db_session)

    with patch.object(db_session, "commit") as mock_commit:
        permitido_msg, texto_msg = verificar_cupo_ia(db_session, usuario, "mensaje")
        assert permitido_msg is True
        assert texto_msg is None

        permitido_arch, texto_arch = verificar_cupo_ia(db_session, usuario, "archivo")
        assert permitido_arch is True
        assert texto_arch is None

        mock_commit.assert_not_called()


def test_tope_texto_etapa_ia(db_session: Session):
    """a) texto en etapa_ia.procesar_llamada_ia_y_normalizacion con TEXTO_TOPE_MENSAJES_USUARIO"""
    usuario = _crear_usuario_test(db_session)
    wamid = f"wamid_test_text_{uuid4().hex[:6]}"
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={"text": {"body": "gasté 1500 en almuerzo"}},
        wamid=wamid,
        from_number=usuario.telefono_normalizado,
        msg_type="text",
        t_inicio=0.0,
        db=db_session,
        usuario=usuario,
        mensaje_texto="gasté 1500 en almuerzo",
    )

    with patch("app.services.uso_ia_service.verificar_cupo_ia", return_value=(False, TEXTO_TOPE_MENSAJES_USUARIO)), \
         patch("app.services.whatsapp_service.enviar_whatsapp") as mock_enviar, \
         patch("app.services.ai_service.procesar_mensaje") as mock_ai, \
         patch("app.routers.whatsapp.media._transcribir_audio") as mock_transcribir, \
         patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_imagen") as mock_extraer_img, \
         patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_texto_pdf") as mock_extraer_pdf:

        procesar_llamada_ia_y_normalizacion(ctx)

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[0][1] == TEXTO_TOPE_MENSAJES_USUARIO
    mock_ai.assert_not_called()
    mock_transcribir.assert_not_called()
    mock_extraer_img.assert_not_called()
    mock_extraer_pdf.assert_not_called()
    assert ctx.terminado is True

    conv = db_session.scalar(select(ConversacionWpp).where(ConversacionWpp.wamid == wamid))
    assert conv is not None
    assert conv.intent_detectado == "tope_superado"
    assert conv.mensaje_bot == TEXTO_TOPE_MENSAJES_USUARIO
    assert conv.tipo_mensaje in ("texto", TipoMensajeWpp.TEXTO)


def test_tope_audio_etapa_entrada(db_session: Session):
    """b) audio en etapa_entrada con TEXTO_GLOBAL_SIN_IA"""
    usuario = _crear_usuario_test(db_session)
    wamid = f"wamid_test_audio_{uuid4().hex[:6]}"
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={"audio": {"id": "media_audio_id_123", "mime_type": "audio/ogg"}},
        wamid=wamid,
        from_number=usuario.telefono_normalizado,
        msg_type="audio",
        t_inicio=0.0,
        db=db_session,
        usuario=usuario,
    )

    with patch("app.services.uso_ia_service.verificar_cupo_ia", return_value=(False, TEXTO_GLOBAL_SIN_IA)), \
         patch("app.services.whatsapp_service.enviar_whatsapp") as mock_enviar, \
         patch("app.services.ai_service.procesar_mensaje") as mock_ai, \
         patch("app.routers.whatsapp.etapa_entrada._transcribir_audio") as mock_transcribir, \
         patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_imagen") as mock_extraer_img, \
         patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_texto_pdf") as mock_extraer_pdf:

        procesar_entrada_medios_y_texto(ctx)

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[0][1] == TEXTO_GLOBAL_SIN_IA
    mock_ai.assert_not_called()
    mock_transcribir.assert_not_called()
    mock_extraer_img.assert_not_called()
    mock_extraer_pdf.assert_not_called()
    assert ctx.terminado is True

    conv = db_session.scalar(select(ConversacionWpp).where(ConversacionWpp.wamid == wamid))
    assert conv is not None
    assert conv.intent_detectado == "tope_superado"
    assert conv.mensaje_bot == TEXTO_GLOBAL_SIN_IA
    assert conv.tipo_mensaje in ("audio", TipoMensajeWpp.AUDIO)


def test_tope_foto_etapa_entrada(db_session: Session):
    """c) foto en etapa_entrada con TEXTO_GLOBAL_SIN_ARCHIVOS"""
    usuario = _crear_usuario_test(db_session)
    wamid = f"wamid_test_foto_{uuid4().hex[:6]}"
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={"image": {"id": "media_img_id_123", "mime_type": "image/jpeg", "caption": "comprobante"}},
        wamid=wamid,
        from_number=usuario.telefono_normalizado,
        msg_type="image",
        t_inicio=0.0,
        db=db_session,
        usuario=usuario,
    )

    with patch("app.routers.whatsapp.etapa_entrada._descargar_medio_meta", return_value=(b"fake_image_bytes", "image/jpeg")), \
         patch("app.services.uso_ia_service.verificar_cupo_ia", return_value=(False, TEXTO_GLOBAL_SIN_ARCHIVOS)), \
         patch("app.services.whatsapp_service.enviar_whatsapp") as mock_enviar, \
         patch("app.services.ai_service.procesar_mensaje") as mock_ai, \
         patch("app.routers.whatsapp.media._transcribir_audio") as mock_transcribir, \
         patch("app.routers.whatsapp.etapa_entrada.extraer_movimientos_de_imagen") as mock_extraer_img, \
         patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_texto_pdf") as mock_extraer_pdf:

        procesar_entrada_medios_y_texto(ctx)

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[0][1] == TEXTO_GLOBAL_SIN_ARCHIVOS
    mock_ai.assert_not_called()
    mock_transcribir.assert_not_called()
    mock_extraer_img.assert_not_called()
    mock_extraer_pdf.assert_not_called()
    assert ctx.terminado is True

    conv = db_session.scalar(select(ConversacionWpp).where(ConversacionWpp.wamid == wamid))
    assert conv is not None
    assert conv.intent_detectado == "tope_superado"
    assert conv.mensaje_bot == TEXTO_GLOBAL_SIN_ARCHIVOS
    assert conv.tipo_mensaje in ("imagen", TipoMensajeWpp.IMAGEN)


def test_tope_pdf_etapa_entrada(db_session: Session):
    """d) PDF en etapa_entrada con TEXTO_TOPE_ARCHIVOS_USUARIO"""
    usuario = _crear_usuario_test(db_session)
    wamid = f"wamid_test_pdf_{uuid4().hex[:6]}"
    ctx = ContextoMensaje(
        datos_mensaje={},
        msg={"document": {"id": "media_pdf_id_123", "mime_type": "application/pdf", "filename": "factura.pdf", "caption": "factura"}},
        wamid=wamid,
        from_number=usuario.telefono_normalizado,
        msg_type="document",
        t_inicio=0.0,
        db=db_session,
        usuario=usuario,
    )

    with patch("app.routers.whatsapp.media.descargar_documento_meta", return_value=(b"%PDF-fake", "application/pdf", None)), \
         patch("app.routers.whatsapp.pdf_documento.leer_texto_pdf", return_value=("Texto extraído factura total $5000", None)), \
         patch("app.services.uso_ia_service.verificar_cupo_ia", return_value=(False, TEXTO_TOPE_ARCHIVOS_USUARIO)), \
         patch("app.services.whatsapp_service.enviar_whatsapp") as mock_enviar, \
         patch("app.services.ai_service.procesar_mensaje") as mock_ai, \
         patch("app.routers.whatsapp.media._transcribir_audio") as mock_transcribir, \
         patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_imagen") as mock_extraer_img, \
         patch("app.routers.whatsapp.extraccion_documento.extraer_movimientos_de_texto_pdf") as mock_extraer_pdf:

        procesar_entrada_medios_y_texto(ctx)

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[0][1] == TEXTO_TOPE_ARCHIVOS_USUARIO
    mock_ai.assert_not_called()
    mock_transcribir.assert_not_called()
    mock_extraer_img.assert_not_called()
    mock_extraer_pdf.assert_not_called()
    assert ctx.terminado is True

    conv = db_session.scalar(select(ConversacionWpp).where(ConversacionWpp.wamid == wamid))
    assert conv is not None
    assert conv.intent_detectado == "tope_superado"
    assert conv.mensaje_bot == TEXTO_TOPE_ARCHIVOS_USUARIO
    assert conv.tipo_mensaje in ("imagen", TipoMensajeWpp.IMAGEN)


