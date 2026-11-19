# -*- coding: utf-8 -*-
"""
Pruebas de cambio de modelo por defecto y umbral calibrado (TC1 a TC4).
"""
from __future__ import annotations

from decimal import Decimal
import json
import logging
import sqlite3
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
from app.routers.whatsapp import extraccion_documento, media, regla_registro
from app.services import (
    ai_service,
    categoria_service,
    clasificador_concepto_service,
    uso_ia_service,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def _compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def _bloquear_openai_real(monkeypatch):
    """Falla si algún test intenta instanciar un cliente real de OpenAI."""
    orig_bind = types.Uuid.bind_processor

    def _safe_uuid_proc(self, dialect):
        proc = orig_bind(self, dialect)
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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    def _prohibido(*_a, **_kw):
        raise AssertionError("Prohibido crear cliente real de OpenAI en tests.")

    monkeypatch.setattr("app.services.openai_client.OpenAI", _prohibido)
    monkeypatch.setattr("app.services.openai_client._openai_client", None)
    categoria_service.invalidar_cache_categorias_globales()
    clasificador_concepto_service.limpiar_caches_clasificador()
    ai_service.invalidar_cache_schema()


@pytest.fixture()
def entorno_modelo(monkeypatch):
    """Prepara SQLite en memoria y un cliente falso que registra kwargs y uso."""
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
        email=f"m2d2_{uuid.uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="Luna",
    )
    db.add(usuario)
    db.commit()

    llamadas_chat: list[dict] = []
    llamadas_audio: list[dict] = []
    usos: list[dict] = []
    cfg = {"fallar_strict": False, "contenido_chat": None}

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
            return SimpleNamespace(text="cinco mil en kiosco")

    class _FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_FakeCompletions())
            self.audio = SimpleNamespace(transcriptions=_FakeAudioTranscriptions())

        def with_options(self, **_kwargs):
            return self

    fake_client = _FakeClient()
    for mod in (
        "app.services.ai_service.get_openai_client",
        "app.services.clasificador_concepto_service.get_openai_client",
        "app.routers.whatsapp.extraccion_documento.get_openai_client",
        "app.routers.whatsapp.media.get_openai_client",
    ):
        monkeypatch.setattr(mod, lambda: fake_client)

    def _fake_registrar_uso(
        usuario_id, funcion, modelo, respuesta=None, segundos_audio=0
    ):
        usos.append(
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
            usos=usos,
            cfg=cfg,
        )
    finally:
        db.close()


def test_tc1_settings_por_defecto():
    """TC1: defaults en settings coinciden con D-M1 y D-M2."""
    assert settings.OPENAI_MODEL == "gpt-6-luna"
    assert settings.OPENAI_MODEL_DOCUMENTOS == "gpt-6-luna"
    assert settings.OPENAI_MODEL_AUDIO == "gpt-transcribe"
    assert settings.OPENAI_RAZONAMIENTO == "none"
    assert settings.UMBRAL_CONFIANZA_IA == 0.70


def test_tc2_kwargs_y_uso_con_defaults(entorno_modelo, monkeypatch):
    """TC2: todas las llamadas usan gpt-6-luna/gpt-transcribe y kwargs esperados."""
    # 1. procesar_mensaje con Structured Outputs
    ai_service.procesar_mensaje("hola", entorno_modelo.usuario, entorno_modelo.db)
    kw1 = entorno_modelo.llamadas_chat[-1]
    assert kw1["model"] == "gpt-6-luna"
    assert kw1["reasoning_effort"] == "none"
    assert kw1["temperature"] == 0.1
    assert kw1["max_completion_tokens"] == 2000
    assert "max_tokens" not in kw1
    assert entorno_modelo.usos[-1]["modelo"] == "gpt-6-luna"

    # 2. procesar_mensaje con fallback json_object
    entorno_modelo.cfg["fallar_strict"] = True
    ai_service.procesar_mensaje(
        "hola otra vez", entorno_modelo.usuario, entorno_modelo.db
    )
    kw2 = entorno_modelo.llamadas_chat[-1]
    assert kw2["model"] == "gpt-6-luna"
    assert kw2["reasoning_effort"] == "none"
    assert kw2["temperature"] == 0.1
    assert kw2["max_completion_tokens"] == 2000
    assert "max_tokens" not in kw2
    assert kw2["response_format"] == {"type": "json_object"}
    assert entorno_modelo.usos[-1]["modelo"] == "gpt-6-luna"

    # 3. clasificador
    entorno_modelo.cfg["contenido_chat"] = json.dumps(
        {"opciones": ["Alimentación › Supermercado"]}
    )
    clasificador_concepto_service.clasificar_sin_limites(
        entorno_modelo.db, "supermercado", "egreso"
    )
    kw3 = entorno_modelo.llamadas_chat[-1]
    assert kw3["model"] == "gpt-6-luna"
    assert kw3["reasoning_effort"] == "none"
    assert kw3["temperature"] == 0
    assert kw3["max_completion_tokens"] == 120
    assert "max_tokens" not in kw3
    assert entorno_modelo.usos[-1]["modelo"] == "gpt-6-luna"

    # 4 y 5. imagen y PDF
    entorno_modelo.cfg["contenido_chat"] = json.dumps(
        {
            "documento_tipo": "ticket",
            "movimientos": [],
            "cuotas": [],
            "empresa": None,
            "vencimiento": None,
        }
    )
    extraccion_documento.extraer_movimientos_de_imagen(
        b"fakeimg", mime_type="image/jpeg", usuario_id=entorno_modelo.usuario.id
    )
    kw4 = entorno_modelo.llamadas_chat[-1]
    assert kw4["model"] == "gpt-6-luna"
    assert kw4["reasoning_effort"] == "none"
    assert kw4["temperature"] == 0
    assert kw4["max_completion_tokens"] == 6000
    assert "max_tokens" not in kw4
    assert entorno_modelo.usos[-1]["modelo"] == "gpt-6-luna"

    extraccion_documento.extraer_movimientos_de_texto_pdf(
        "Resumen de prueba con monto 1000 pesos del supermercado",
        usuario_id=entorno_modelo.usuario.id,
    )
    kw5 = entorno_modelo.llamadas_chat[-1]
    assert kw5["model"] == "gpt-6-luna"
    assert kw5["reasoning_effort"] == "none"
    assert kw5["temperature"] == 0
    assert kw5["max_completion_tokens"] == 6000
    assert "max_tokens" not in kw5
    assert entorno_modelo.usos[-1]["modelo"] == "gpt-6-luna"

    # 6. Transcripción de audio
    monkeypatch.setattr(
        "app.routers.whatsapp.media._descargar_medio_meta",
        lambda _mid: (b"OggS" + b"\x00" * 30, "audio/ogg"),
    )
    txt, err = media._transcribir_audio(
        "media_m2d2", usuario_id=entorno_modelo.usuario.id
    )
    assert err is None
    assert txt == "cinco mil en kiosco"
    kw_aud = entorno_modelo.llamadas_audio[-1]
    assert kw_aud["model"] == "gpt-transcribe"
    assert kw_aud["language"] == "es"
    assert entorno_modelo.usos[-1]["modelo"] == "gpt-transcribe"


def test_tc3_confianza_ia_baja_umbral_070():
    """TC3: con umbral 0.70, 0.70 no es baja y 0.69 sí es baja."""
    assert regla_registro.confianza_ia_baja(0.70) is False
    assert regla_registro.confianza_ia_baja(0.69) is True


def test_tc4_calcular_costo_ia_luna_y_transcribe(caplog):
    """TC4: usa precios de gpt-6-luna (sin fallback gpt-4o) y gpt-transcribe."""
    with caplog.at_level(logging.WARNING):
        costo_luna = uso_ia_service.calcular_costo_ia(
            modelo="gpt-6-luna",
            tokens_entrada=2_000_000,
            tokens_entrada_cache=1_000_000,
            tokens_salida=1_000_000,
        )
        costo_audio = uso_ia_service.calcular_costo_ia(
            modelo="gpt-transcribe",
            segundos_audio=120,
        )
    assert costo_luna == Decimal("0.610000")
    assert costo_audio == Decimal("0.009000")
    assert "Modelo desconocido" not in caplog.text
    assert "sin precio propio" not in caplog.text
