# -*- coding: utf-8 -*-
"""
tests/test_modelos_ia_m2.py — Pruebas de D-M2.2, D-M2.3 y D-M2.4 (TM1 a TM4).
"""
from __future__ import annotations

from decimal import Decimal
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.core.config import settings
from app.core.database import Base
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.usuario import (
    AuthProvider,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.routers.whatsapp import extraccion_documento, media
from app.services import (
    ai_service,
    categoria_service,
    clasificador_concepto_service,
    modelos_ia,
    uso_ia_service,
)
from app.services.importacion import parser_bna_mastercard, parser_generico


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def bloquear_openai(monkeypatch):
    """Falla si algún test intenta instanciar un cliente real de OpenAI."""
    orig_uuid_proc = types.Uuid.bind_processor

    def _safe_uuid_proc(self, dialect):
        proc = orig_uuid_proc(self, dialect)
        if proc is None:
            return None

        def _proc(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return _proc

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_proc)
    import sqlite3

    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    def _prohibido(*args, **kwargs):
        raise AssertionError("Prohibido crear cliente real de OpenAI en tests.")

    monkeypatch.setattr("app.services.openai_client.OpenAI", _prohibido)
    monkeypatch.setattr("app.services.openai_client._openai_client", None)
    categoria_service.invalidar_cache_categorias_globales()
    clasificador_concepto_service.limpiar_caches_clasificador()
    ai_service.invalidar_cache_schema()


@pytest.fixture()
def entorno_ia(monkeypatch):
    """Prepara SQLite en memoria y un cliente falso que registra cada llamada."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models

    _ = app.models
    Base.metadata.create_all(bind=engine)
    SessionTest = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionTest()

    for cat_def in ARBOL_CATEGORIAS:
        cat = Categoria(
            id=uuid.uuid4(),
            nombre=cat_def.nombre,
            tipo=TipoCategoria(cat_def.tipo),
            orden=cat_def.orden,
            icono=cat_def.icono,
            color=cat_def.color,
            estado=EstadoCategoria.ACTIVA,
        )
        db.add(cat)
        db.flush()
        for sub_def in cat_def.subcategorias:
            db.add(
                Subcategoria(
                    id=uuid.uuid4(),
                    categoria_id=cat.id,
                    nombre=sub_def.nombre,
                    orden=sub_def.orden,
                    es_sistema=sub_def.es_sistema,
                    estado=EstadoSubcategoria.ACTIVA,
                )
            )

    usuario = Usuario(
        id=uuid.uuid4(),
        email=f"mod_{uuid.uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="Modelos",
    )
    db.add(usuario)
    db.commit()

    llamadas_chat: list[dict] = []
    llamadas_audio: list[dict] = []
    usos_registrados: list[dict] = []
    cfg = {"fallar_strict": False, "contenido_chat": None, "texto_audio": "dos mil"}

    class _FakeCompletions:
        def create(self, **kwargs):
            llamadas_chat.append(dict(kwargs))
            if cfg["fallar_strict"]:
                cfg["fallar_strict"] = False
                raise RuntimeError("strict schema error")
            contenido = cfg["contenido_chat"] or json.dumps(
                {
                    "intent": "saludo",
                    "entidades": {},
                    "confianza": 0.9,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Hola",
                }
            )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=contenido),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=20,
                    prompt_tokens_details=None,
                ),
            )

    class _FakeAudioTranscriptions:
        def create(self, **kwargs):
            llamadas_audio.append(dict(kwargs))
            return SimpleNamespace(text=cfg["texto_audio"])

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())
            self.audio = SimpleNamespace(transcriptions=_FakeAudioTranscriptions())

        def with_options(self, **kwargs):
            return self

    fake_client = _FakeClient()
    for mod in (
        "app.services.ai_service.get_openai_client",
        "app.services.clasificador_concepto_service.get_openai_client",
        "app.routers.whatsapp.extraccion_documento.get_openai_client",
        "app.routers.whatsapp.media.get_openai_client",
        "app.services.importacion.parser_bna_mastercard.get_openai_client",
        "app.services.importacion.parser_generico.get_openai_client",
    ):
        monkeypatch.setattr(mod, lambda: fake_client)

    def _fake_registrar_uso(
        usuario_id, funcion, modelo, respuesta=None, segundos_audio=0
    ):
        usos_registrados.append(
            {
                "usuario_id": usuario_id,
                "funcion": funcion,
                "modelo": modelo,
                "segundos_audio": segundos_audio,
            }
        )

    monkeypatch.setattr(
        "app.services.uso_ia_service.registrar_uso_ia", _fake_registrar_uso
    )
    monkeypatch.setattr(
        "app.services.clasificador_concepto_service.registrar_uso_ia",
        _fake_registrar_uso,
    )
    monkeypatch.setattr(
        "app.services.uso_ia_service.verificar_rate_limit",
        lambda *a, **k: (True, 999, 0),
    )
    monkeypatch.setattr(
        "app.services.uso_ia_service.avisar_costo_si_corresponde",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("app.services.uso_ia_service.SessionLocal", SessionTest)

    try:
        yield SimpleNamespace(
            db=db,
            usuario=usuario,
            llamadas_chat=llamadas_chat,
            llamadas_audio=llamadas_audio,
            usos_registrados=usos_registrados,
            cfg=cfg,
        )
    finally:
        db.close()


def test_tm1_kwargs_iguales_a_head_en_las_ocho_llamadas(entorno_ia, monkeypatch):
    """TM1: con los defaults, las 8 llamadas mandan los mismos kwargs que HEAD."""
    # 1. procesar_mensaje con Structured Outputs
    ai_service.procesar_mensaje("hola", entorno_ia.usuario, entorno_ia.db)
    kw1 = entorno_ia.llamadas_chat[-1]
    assert kw1["model"] == "gpt-4o-mini-2024-07-18"
    assert kw1["temperature"] == 0.1
    assert kw1["max_tokens"] == 2000
    assert kw1["response_format"]["type"] == "json_schema"
    assert isinstance(kw1["messages"], list) and len(kw1["messages"]) >= 2

    # 2. procesar_mensaje con fallback json_object
    entorno_ia.cfg["fallar_strict"] = True
    ai_service.procesar_mensaje("hola de nuevo", entorno_ia.usuario, entorno_ia.db)
    kw2 = entorno_ia.llamadas_chat[-1]
    assert kw2["model"] == "gpt-4o-mini-2024-07-18"
    assert kw2["temperature"] == 0.1
    assert kw2["max_tokens"] == 2000
    assert kw2["response_format"] == {"type": "json_object"}
    assert isinstance(kw2["messages"], list)

    # 3 y 4. clasificador (clasificar_sin_limites y clasificar_concepto)
    entorno_ia.cfg["contenido_chat"] = json.dumps(
        {"opciones": ["Alimentación › Supermercado"]}
    )
    clasificador_concepto_service.clasificar_sin_limites(
        entorno_ia.db, "supermercado", "egreso"
    )
    kw3 = entorno_ia.llamadas_chat[-1]
    assert kw3["model"] == "gpt-4o-mini-2024-07-18"
    assert kw3["temperature"] == 0
    assert kw3["max_tokens"] == 120
    assert kw3["response_format"]["type"] == "json_schema"
    assert len(kw3["messages"]) == 2

    clasificador_concepto_service.clasificar_concepto(
        entorno_ia.db, entorno_ia.usuario, "verduleria", "egreso"
    )
    kw4 = entorno_ia.llamadas_chat[-1]
    assert kw4["model"] == "gpt-4o-mini-2024-07-18"
    assert kw4["temperature"] == 0
    assert kw4["max_tokens"] == 120
    assert kw4["response_format"]["type"] == "json_schema"
    assert len(kw4["messages"]) == 2

    # 5 y 6. extraccion_documento (imagen y PDF)
    entorno_ia.cfg["contenido_chat"] = json.dumps(
        {
            "documento_tipo": "ticket",
            "movimientos": [],
            "cuotas": [],
            "empresa": None,
            "vencimiento": None,
        }
    )
    extraccion_documento.extraer_movimientos_de_imagen(
        b"fakeimg", mime_type="image/jpeg", usuario_id=entorno_ia.usuario.id
    )
    kw5 = entorno_ia.llamadas_chat[-1]
    assert kw5["model"] == "gpt-4o"
    assert kw5["temperature"] == 0
    assert "max_tokens" not in kw5
    assert "max_completion_tokens" not in kw5
    assert kw5["response_format"]["type"] == "json_schema"
    assert len(kw5["messages"]) == 2

    extraccion_documento.extraer_movimientos_de_texto_pdf(
        "Resumen de prueba con monto 1000 pesos del supermercado",
        usuario_id=entorno_ia.usuario.id,
    )
    kw6 = entorno_ia.llamadas_chat[-1]
    assert kw6["model"] == "gpt-4o"
    assert kw6["temperature"] == 0
    assert "max_tokens" not in kw6
    assert "max_completion_tokens" not in kw6
    assert kw6["response_format"]["type"] == "json_schema"
    assert len(kw6["messages"]) == 2

    # 7 y 8. parsers de importación
    class _FakePdf:
        pages = [
            SimpleNamespace(
                extract_text=lambda: (
                    "RESUMEN TARJETA DE CREDITO CONSUMO EN SUPERMERCADO "
                    "COTO 15/10/2026 POR UN TOTAL DE PESOS 25000,00 FINAL."
                )
            )
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("pdfplumber.open", lambda *a, **k: _FakePdf())
    entorno_ia.cfg["contenido_chat"] = json.dumps({"transacciones": []})

    parser_bna_mastercard.parsear_bna_mastercard(b"%PDF-fake")
    kw7 = entorno_ia.llamadas_chat[-1]
    assert kw7["model"] == settings.OPENAI_MODEL
    assert kw7["temperature"] == 0.1
    assert kw7["max_tokens"] == 6000
    assert kw7["response_format"] == {"type": "json_object"}
    assert len(kw7["messages"]) == 2

    parser_generico.parsear_generico(b"%PDF-fake")
    kw8 = entorno_ia.llamadas_chat[-1]
    assert kw8["model"] == settings.OPENAI_MODEL
    assert kw8["temperature"] == 0.1
    assert kw8["max_tokens"] == 6000
    assert kw8["response_format"] == {"type": "json_object"}
    assert len(kw8["messages"]) == 2


def test_tm2_parametros_chat_y_es_modelo_razonamiento(monkeypatch):
    """TM2: parametros_chat para gpt-6-luna, gpt-4o-mini y gpt-4o."""
    assert modelos_ia.es_modelo_razonamiento("gpt-6-luna") is True
    assert modelos_ia.es_modelo_razonamiento("gpt-5") is True
    assert modelos_ia.es_modelo_razonamiento("gpt-4o") is False
    assert modelos_ia.es_modelo_razonamiento("gpt-4o-mini-2024-07-18") is False
    assert modelos_ia.es_modelo_razonamiento("gpt-4.1") is False

    monkeypatch.setattr(settings, "OPENAI_RAZONAMIENTO", "none")
    assert modelos_ia.parametros_chat("gpt-6-luna", 0.1, 2000) == {
        "reasoning_effort": "none",
        "max_completion_tokens": 2000,
        "temperature": 0.1,
    }

    monkeypatch.setattr(settings, "OPENAI_RAZONAMIENTO", "low")
    assert modelos_ia.parametros_chat("gpt-6-luna", 0.1, 2000) == {
        "reasoning_effort": "low",
        "max_completion_tokens": 6000,
    }

    assert modelos_ia.parametros_chat("gpt-4o-mini-2024-07-18", 0.1, 2000) == {
        "temperature": 0.1,
        "max_tokens": 2000,
    }
    assert modelos_ia.parametros_chat("gpt-4o", 0, None) == {"temperature": 0}


def test_tm3_settings_en_imagen_y_audio(entorno_ia, monkeypatch):
    """TM3: imagen y audio usan el setting y lo pasan a registrar_uso_ia."""
    monkeypatch.setattr(settings, "OPENAI_MODEL_DOCUMENTOS", "gpt-6-luna")
    monkeypatch.setattr(settings, "OPENAI_RAZONAMIENTO", "none")
    entorno_ia.cfg["contenido_chat"] = json.dumps(
        {
            "documento_tipo": "ticket",
            "movimientos": [],
            "cuotas": [],
            "empresa": None,
            "vencimiento": None,
        }
    )
    extraccion_documento.extraer_movimientos_de_imagen(
        b"fakeimg", mime_type="image/jpeg", usuario_id=entorno_ia.usuario.id
    )
    assert entorno_ia.llamadas_chat[-1]["model"] == "gpt-6-luna"
    assert entorno_ia.usos_registrados[-1]["modelo"] == "gpt-6-luna"

    monkeypatch.setattr(settings, "OPENAI_MODEL_AUDIO", "gpt-transcribe")
    monkeypatch.setattr(
        "app.routers.whatsapp.media._descargar_medio_meta",
        lambda media_id: (b"OggS" + b"\x00" * 30, "audio/ogg"),
    )
    txt, err = media._transcribir_audio(
        "media_1", usuario_id=entorno_ia.usuario.id
    )
    assert err is None
    assert txt == "dos mil"
    assert entorno_ia.llamadas_audio[-1]["model"] == "gpt-transcribe"
    assert entorno_ia.llamadas_audio[-1]["language"] == "es"
    assert entorno_ia.usos_registrados[-1]["modelo"] == "gpt-transcribe"


def test_tm4_calcular_costo_ia_precios():
    """TM4: precios de gpt-6-luna, gpt-transcribe, whisper-1 y gpt-4o-mini."""
    assert uso_ia_service.calcular_costo_ia(
        "gpt-6-luna", 1000000, 0, 1000000
    ) == Decimal("0.60")
    assert uso_ia_service.calcular_costo_ia(
        "gpt-transcribe", segundos_audio=60
    ) == Decimal("0.0045")
    assert uso_ia_service.calcular_costo_ia(
        "whisper-1", segundos_audio=60
    ) == Decimal("0.006")
    assert uso_ia_service.calcular_costo_ia(
        "gpt-4o-mini-2024-07-18", 1000000, 0, 0
    ) == Decimal("0.15")
