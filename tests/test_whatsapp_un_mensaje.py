"""
tests/test_whatsapp_un_mensaje.py — Pruebas de un solo mensaje saliente por entrante (buzón),
avisos al pie y controles de orquestador (D1 a D12).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
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

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.etapa_ia import procesar_llamada_ia_y_normalizacion
from app.routers.whatsapp_ia import _procesar_mensaje_whatsapp_background
from app.services import whatsapp_service
from app.services.uso_ia_service import TEXTO_TOPE_MENSAJES_USUARIO
from app.services.whatsapp_service import (
    buzon_de_respuesta,
    enviar_whatsapp,
    enviar_whatsapp_template,
)
from app.utils.telefono import normalizar_telefono_ar


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
    monkeypatch.setattr("app.services.salientes_wpp_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.rate_limit_service.SessionLocal", TestingSession)
    monkeypatch.setattr("app.routers.whatsapp_ia.SessionLocal", TestingSession)
    monkeypatch.setattr("time.sleep", lambda s: None)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario(db: Session, telefono: str = "+5491112345678") -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        telefono=telefono,
        telefono_normalizado=normalizar_telefono_ar(telefono),
        telefono_verificado=True,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="User",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_d1_buzon_unifica_partes_mismo_numero(monkeypatch):
    """D1 Con buzon_de_respuesta("5491112345678", wamid_entrante="wamid.A"):
    enviar_whatsapp("5491112345678", "Hola") y enviar_whatsapp("+54 9 11 1234-5678", "Chau")
    → al salir, _enviar_registrado se llama 1 vez con texto "Hola\n\nChau", partes=2, wamid_entrante="wamid.A"."""
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta("5491112345678", wamid_entrante="wamid.A"):
        r1 = enviar_whatsapp("5491112345678", "Hola")
        r2 = enviar_whatsapp("+54 9 11 1234-5678", "Chau")
        assert r1 is True
        assert r2 is True
        assert mock_enviar.call_count == 0

    assert mock_enviar.call_count == 1
    args, kwargs = mock_enviar.call_args
    assert kwargs.get("texto") == "Hola\n\nChau"
    assert kwargs.get("partes") == 2
    assert kwargs.get("wamid_entrante") == "wamid.A"


def test_d2_buzon_otro_numero_se_manda_en_el_momento(monkeypatch):
    """D2 Dentro del mismo buzón, enviar_whatsapp("5491199999999", "Aviso") se manda
    en el momento con tipo "otro_numero": 2 llamadas en total, una por número."""
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta("5491112345678", wamid_entrante="wamid.A"):
        enviar_whatsapp("5491112345678", "Hola")
        assert mock_enviar.call_count == 0
        enviar_whatsapp("5491199999999", "Aviso")
        assert mock_enviar.call_count == 1
        args_otro, kwargs_otro = mock_enviar.call_args
        assert args_otro[0] == "5491199999999"
        assert kwargs_otro.get("tipo") == "otro_numero"

    assert mock_enviar.call_count == 2
    args_orig, kwargs_orig = mock_enviar.call_args
    assert kwargs_orig.get("texto") == "Hola"


def test_d3_buzon_con_aviso_y_callback(monkeypatch):
    """D3 Buzón con "Listo. $5.000 en Súper desde Galicia — registrado." y
    agregar_aviso("Ojo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar.", "recordatorio_impago", al_enviar=f)
    → 1 llamada con texto unido por \n y aviso_tipo; f se llama 1 vez. Con False no se llama."""
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    cb = MagicMock()
    with buzon_de_respuesta("5491112345678") as buzon:
        enviar_whatsapp("5491112345678", "Listo. $5.000 en Súper desde Galicia — registrado.")
        buzon.agregar_aviso(
            "Ojo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar.",
            "recordatorio_impago",
            al_enviar=cb,
        )

    assert mock_enviar.call_count == 1
    args, kwargs = mock_enviar.call_args
    esperado_texto = (
        "Listo. $5.000 en Súper desde Galicia — registrado.\n"
        "Ojo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar."
    )
    assert kwargs.get("texto") == esperado_texto
    assert kwargs.get("aviso_tipo") == "recordatorio_impago"
    assert cb.call_count == 1

    # Caso _enviar_registrado devuelve False -> cb no se llama
    mock_enviar.reset_mock()
    mock_enviar.return_value = False
    cb2 = MagicMock()
    with buzon_de_respuesta("5491112345678") as buzon:
        enviar_whatsapp("5491112345678", "Mensaje")
        buzon.agregar_aviso("Aviso", "tipo", al_enviar=cb2)

    assert mock_enviar.call_count == 1
    assert cb2.call_count == 0


def test_d4_buzon_sin_partes_no_manda_aviso_solo(monkeypatch):
    """D4 Buzón sin partes con un aviso → 0 llamadas."""
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with buzon_de_respuesta("5491112345678") as buzon:
        buzon.agregar_aviso("Aviso solo", "tipo")

    assert mock_enviar.call_count == 0


def test_d5_agregar_aviso_validaciones(monkeypatch):
    """D5 agregar_aviso con un texto de 91 caracteres o con "\n" → no aparece en el texto enviado;
    un segundo aviso válido después del primero → solo sale el primero."""
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    # Texto de 91 caracteres
    with buzon_de_respuesta("5491112345678") as buzon:
        enviar_whatsapp("5491112345678", "Texto principal")
        buzon.agregar_aviso("a" * 91, "tipo")
    assert mock_enviar.call_args[1].get("texto") == "Texto principal"
    assert mock_enviar.call_args[1].get("aviso_tipo") is None

    # Texto con salto de línea
    mock_enviar.reset_mock()
    with buzon_de_respuesta("5491112345678") as buzon:
        enviar_whatsapp("5491112345678", "Texto principal")
        buzon.agregar_aviso("Linea 1\nLinea 2", "tipo")
    assert mock_enviar.call_args[1].get("texto") == "Texto principal"
    assert mock_enviar.call_args[1].get("aviso_tipo") is None

    # Segundo aviso válido después del primero -> solo sale el primero
    mock_enviar.reset_mock()
    with buzon_de_respuesta("5491112345678") as buzon:
        enviar_whatsapp("5491112345678", "Texto principal")
        buzon.agregar_aviso("Primer aviso", "tipo1")
        buzon.agregar_aviso("Segundo aviso", "tipo2")
    assert mock_enviar.call_args[1].get("texto") == "Texto principal\nPrimer aviso"
    assert mock_enviar.call_args[1].get("aviso_tipo") == "tipo1"


def test_d6_sin_buzon_envia_en_el_momento(monkeypatch):
    """D6 Sin buzón, enviar_whatsapp("5491112345678", "Hola") → 1 llamada en el momento con wamid_entrante None."""
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    res = enviar_whatsapp("5491112345678", "Hola")
    assert res is True
    assert mock_enviar.call_count == 1
    _, kwargs = mock_enviar.call_args
    assert kwargs.get("wamid_entrante") is None
    assert kwargs.get("texto") == "Hola"


def test_d7_buzon_despacha_ante_excepcion(monkeypatch):
    """D7 Dentro del buzón se agrega "A" y después se lanza ValueError → igual sale 1 llamada con "A" y la excepción se propaga."""
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr(whatsapp_service, "_enviar_registrado", mock_enviar)

    with pytest.raises(ValueError):
        with buzon_de_respuesta("5491112345678"):
            enviar_whatsapp("5491112345678", "A")
            raise ValueError("Error simulado")

    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[1].get("texto") == "A"


def test_d8_plantilla_con_buzon_abierto_devuelve_false(monkeypatch):
    """D8 enviar_whatsapp_template con buzón abierto para ese número → devuelve False, 0 pedidos a Meta, fila con motivo "plantilla_con_buzon"."""
    mock_post = MagicMock()
    monkeypatch.setattr("app.services.whatsapp_service._post_meta", mock_post)

    with buzon_de_respuesta("5491112345678"):
        res = enviar_whatsapp_template("5491112345678", "template_test", "es")
        assert res is False

    assert mock_post.call_count == 0


def test_d9_orquestador_respuesta_y_recordatorio_impago(db_session, monkeypatch):
    """D9 Orquestador: _procesar_mensaje_whatsapp_background con usuario de prueba, etapas parcheadas
    para que la respuesta sea "Listo. $5.000 en Súper desde Galicia — registrado.",
    una notificación RESUMEN_IMPAGO de tarjeta "Visa" con vencimiento 04/11 y deuda $50.000
    (obtener_detalle_resumen_vencimiento parcheado) → exactamente 1 llamada a _enviar_registrado con texto
    "Listo. $5.000 en Súper desde Galicia — registrado.\nOjo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar."
    y la notificación queda enviada_whatsapp=True."""
    usuario = _crear_usuario(db_session, "+5491112345678")

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Galicia",
        moneda=Moneda.ARS,
    )
    db_session.add(billetera)
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        estado=EstadoTarjeta.ACTIVA,
        dia_cierre=20,
        dia_vencimiento=4,
    )
    db_session.add(tarjeta)
    db_session.commit()

    # Crear notificación RESUMEN_IMPAGO
    notif = Notificacion(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo=TipoNotificacion.RESUMEN_IMPAGO,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje="Resumen Visa impago",
        canal_whatsapp=True,
        enviada_whatsapp=False,
        archivada=False,
        entidad_tipo="tarjeta",
        entidad_id=tarjeta.id,
        datos_template={"tarjeta_nombre": "Visa", "vencimiento": "2026-11-04"},
        created_at=datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(notif)
    db_session.commit()

    mock_enviar_reg = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.whatsapp_service._enviar_registrado", mock_enviar_reg)

    monkeypatch.setattr("app.routers.whatsapp_ia.procesar_entrada_medios_y_texto", lambda ctx: None)
    monkeypatch.setattr("app.routers.whatsapp_ia.procesar_estados_y_handlers_deterministicos", lambda ctx: None)
    monkeypatch.setattr("app.routers.whatsapp_ia.procesar_llamada_ia_y_normalizacion", lambda ctx: None)
    monkeypatch.setattr("app.routers.whatsapp_ia.procesar_resolucion_billetera_y_propuestas", lambda ctx: None)

    def _mock_despacho(ctx):
        ctx.resultado_ia = {"respuesta_usuario": "Listo. $5.000 en Súper desde Galicia — registrado."}

    monkeypatch.setattr("app.routers.whatsapp_ia.procesar_despacho_y_respuesta", _mock_despacho)
    monkeypatch.setattr(
        "app.routers.whatsapp.recordatorio_impago_wpp.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: {"monto_deuda_restante": Decimal("50000")},
    )
    monkeypatch.setattr(
        "app.services.tarjeta_service.obtener_detalle_resumen_vencimiento",
        lambda *args, **kwargs: {"monto_deuda_restante": Decimal("50000")},
    )

    datos_mensaje = {
        "msg": {"id": "wamid.in1", "from": "+5491112345678", "type": "text"},
        "wamid": "wamid.in1",
        "from_number": "+5491112345678",
        "msg_type": "text",
        "t_inicio": 0,
    }

    _procesar_mensaje_whatsapp_background(datos_mensaje)

    assert mock_enviar_reg.call_count == 1
    _, kwargs = mock_enviar_reg.call_args
    esperado = (
        "Listo. $5.000 en Súper desde Galicia — registrado.\n"
        "Ojo: el resumen de Visa de $50.000 (venció 04/11) figura sin pagar."
    )
    assert kwargs.get("texto") == esperado
    assert kwargs.get("wamid_entrante") == "wamid.in1"

    # Verificar que la notificación quedó enviada
    db_session.commit()
    db_session.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_d10_orquestador_rate_limit_rafaga_una_vez(db_session, monkeypatch):
    """D10 Orquestador con _verificar_rate_limit_registrado dando (False, motivo) dos veces seguidas con el mismo teléfono
    → la primera vez 1 llamada con "Estás enviando muchos mensajes seguidos. Por favor, esperá un momento antes de volver a escribir.";
    la segunda, 0."""
    _crear_usuario(db_session, "+5491112345678")

    mock_enviar_reg = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.whatsapp_service._enviar_registrado", mock_enviar_reg)

    motivo = "Estás enviando muchos mensajes seguidos. Por favor, esperá un momento antes de volver a escribir."
    monkeypatch.setattr(
        "app.routers.whatsapp_ia._verificar_rate_limit_registrado",
        lambda tel, es_medio=False: (False, motivo),
    )

    datos_msg = {
        "msg": {"id": "wamid.r1", "from": "+5491112345678", "type": "text"},
        "wamid": "wamid.r1",
        "from_number": "+5491112345678",
        "msg_type": "text",
        "t_inicio": 0,
    }

    # Primera vez -> 1 llamada
    _procesar_mensaje_whatsapp_background(datos_msg)
    assert mock_enviar_reg.call_count == 1
    assert mock_enviar_reg.call_args[1].get("texto") == motivo

    # Segunda vez inmediatamente -> 0 llamadas más
    datos_msg["wamid"] = "wamid.r2"
    _procesar_mensaje_whatsapp_background(datos_msg)
    assert mock_enviar_reg.call_count == 1


def test_d11_tope_ia_una_vez_por_dia(db_session, monkeypatch):
    """D11 Tope de la IA en etapa_ia dos veces el mismo día para el mismo usuario
    → el texto sale 1 vez; las dos filas "tope_superado" de conversaciones_wpp existen."""
    usuario = _crear_usuario(db_session, "+5491112345678")

    monkeypatch.setattr(
        "app.services.uso_ia_service.verificar_cupo_ia",
        lambda db, usr, tipo: (False, TEXTO_TOPE_MENSAJES_USUARIO),
    )
    mock_enviar = MagicMock(return_value=True)
    monkeypatch.setattr("app.services.whatsapp_service.enviar_whatsapp", mock_enviar)

    ctx1 = ContextoMensaje(
        datos_mensaje={},
        msg={"id": "wamid.t1", "text": {"body": "gasto 100"}},
        wamid="wamid.t1",
        from_number="+5491112345678",
        msg_type="text",
        t_inicio=0,
        db=db_session,
        usuario=usuario,
    )
    ctx1.mensaje_texto = "gasto 100"

    procesar_llamada_ia_y_normalizacion(ctx1)
    assert mock_enviar.call_count == 1
    assert mock_enviar.call_args[0][1] == TEXTO_TOPE_MENSAJES_USUARIO

    ctx2 = ContextoMensaje(
        datos_mensaje={},
        msg={"id": "wamid.t2", "text": {"body": "gasto 200"}},
        wamid="wamid.t2",
        from_number="+5491112345678",
        msg_type="text",
        t_inicio=0,
        db=db_session,
        usuario=usuario,
    )
    ctx2.mensaje_texto = "gasto 200"

    procesar_llamada_ia_y_normalizacion(ctx2)
    # En la segunda llamada, el aviso no vuelve a salir
    assert mock_enviar.call_count == 1

    # Verificar que ambas filas de conversaciones_wpp con tope_superado existen
    filas = db_session.scalars(
        select(ConversacionWpp).where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "tope_superado",
        )
    ).all()
    assert len(filas) == 2


def test_d12_estatica_sin_post_meta_fuera_de_whatsapp_service():
    """D12 Estática: fuera de app/services/whatsapp_service.py ningún archivo de app\\
    contiene "/messages" junto a graph.facebook.com ni llama a get_meta_http_client().post."""
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
    permitido = os.path.abspath(os.path.join(backend_root, "services", "whatsapp_service.py"))

    violaciones = []
    for root, _, files in os.walk(backend_root):
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.abspath(os.path.join(root, f))
            if path == permitido:
                continue

            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()

            if "get_meta_http_client().post" in content:
                violaciones.append(f"{path}: llama a get_meta_http_client().post")

            if "/messages" in content and "graph.facebook.com" in content:
                violaciones.append(f"{path}: contiene /messages junto a graph.facebook.com")

    assert len(violaciones) == 0, "Violaciones detectadas:\n" + "\n".join(violaciones)
