# -*- coding: utf-8 -*-
"""
tests/test_clasificador_concepto_q2.py — Pruebas del clasificador (TC1 a TC9 y F2).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.core.config import settings
from app.core.database import Base
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.uso_ia import UsoIA
from app.models.usuario import (
    AuthProvider,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.services import (
    ai_service,
    categoria_service,
    clasificador_concepto_service,
)
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def setup_sqlite_y_guardia_openai(monkeypatch):
    """Compatibilidad SQLite y bloqueo estricto contra creación real de OpenAI."""
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

    def _prohibido_openai(*args, **kwargs):
        raise AssertionError("Prohibido crear cliente real de OpenAI en tests.")

    monkeypatch.setattr("app.services.openai_client.OpenAI", _prohibido_openai)
    monkeypatch.setattr("app.services.openai_client._openai_client", None)
    monkeypatch.setattr(
        "app.services.clasificador_concepto_service.get_openai_client",
        _prohibido_openai,
    )

    categoria_service.invalidar_cache_categorias_globales()
    clasificador_concepto_service.limpiar_caches_clasificador()
    yield
    categoria_service.invalidar_cache_categorias_globales()
    clasificador_concepto_service.limpiar_caches_clasificador()


def _poblar_catalogo(db: Session) -> None:
    """Carga categorías y subcategorías del catálogo real en SQLite."""
    for cat_def in ARBOL_CATEGORIAS:
        cat = Categoria(
            id=uuid4(),
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
            sub = Subcategoria(
                id=uuid4(),
                categoria_id=cat.id,
                nombre=sub_def.nombre,
                orden=sub_def.orden,
                es_sistema=sub_def.es_sistema,
                estado=EstadoSubcategoria.ACTIVA,
            )
            db.add(sub)
    db.commit()


def _crear_usuario(db: Session, prefijo: str) -> Usuario:
    """Crea un usuario de prueba en la base SQLite."""
    u = Usuario(
        id=uuid4(),
        email=f"{prefijo}_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="Clasificador",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture(name="ctx_clf", scope="function")
def ctx_clf_fixture(monkeypatch):
    """Base SQLite con catálogo, usuario y fake configurable de llamar_clasificador."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models

    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr("app.services.uso_ia_service.SessionLocal", TestingSession)
    monkeypatch.setattr(
        "app.services.uso_ia_service.avisar_costo_si_corresponde",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.uso_ia_service.verificar_rate_limit",
        lambda *args, **kwargs: (True, 999, 0),
    )

    db = TestingSession()
    _poblar_catalogo(db)
    usuario = _crear_usuario(db, "clf")

    llamadas: list[dict] = []
    respuesta_cfg: dict = {
        "opciones": ["Educación › Libros y útiles"],
        "error": None,
    }

    def _fake_llamar(
        mensajes,
        schema,
        model=None,
        temperature=0,
        max_tokens=120,
    ):
        llamadas.append(
            {
                "mensajes": mensajes,
                "schema": schema,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        if respuesta_cfg["error"] is not None:
            raise respuesta_cfg["error"]
        contenido = json.dumps({"opciones": list(respuesta_cfg["opciones"])})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=contenido))],
            usage=SimpleNamespace(
                prompt_tokens=400,
                completion_tokens=25,
                prompt_tokens_details=None,
            ),
        )

    monkeypatch.setattr(
        "app.services.clasificador_concepto_service.llamar_clasificador",
        _fake_llamar,
    )

    try:
        yield SimpleNamespace(
            db=db,
            usuario=usuario,
            llamadas=llamadas,
            respuesta_cfg=respuesta_cfg,
        )
    finally:
        db.close()


def test_tc1_opciones_validas_excluye_sistema_ahorro_y_otro_tipo(ctx_clf):
    """TC1: opciones_validas filtra tipo y excluye Ahorro y subcategorías es_sistema."""
    eg = clasificador_concepto_service.opciones_validas(ctx_clf.db, "egreso")
    assert "Educación › Libros y útiles" in eg
    assert "Alimentación › Otros" in eg
    assert "Otros › Mascotas" in eg
    assert "Banco › Tarjeta de crédito" not in eg
    assert "Banco › Impuestos" not in eg
    assert not any(op.startswith("Ahorro ›") for op in eg)
    assert "Trabajo › Sueldo" not in eg

    ing = clasificador_concepto_service.opciones_validas(ctx_clf.db, "ingreso")
    assert "Trabajo › Sueldo" in ing
    assert not any(op.startswith("Ahorro ›") for op in ing)
    assert "Educación › Libros y útiles" not in ing
    assert "Alimentación › Otros" not in ing


def test_tc2_system_prompt_reglas_bot_y_catalogo_por_tipo(ctx_clf):
    """TC2: prompt contiene las 5 reglas de ai_service y filtra categorías por tipo."""
    texto_bot = ai_service.SYSTEM_PROMPT
    inicio = texto_bot.index("REGLAS DE CATEGORÍA (OBLIGATORIAS):")
    fin = texto_bot.index("CONCEPTOS RAROS O DESCONOCIDOS:")
    bloque = texto_bot[inicio:fin]
    reglas_bot = [ln for ln in bloque.splitlines() if ln.startswith("- ")]
    assert len(reglas_bot) == 5

    for regla in reglas_bot:
        assert regla in clasificador_concepto_service.SYSTEM_PROMPT_CLASIFICADOR

    prompt_eg = clasificador_concepto_service.construir_system_prompt(
        ctx_clf.db, "egreso"
    )
    for regla in reglas_bot:
        assert regla in prompt_eg
    linea_libros = (
        "  - Libros y útiles: útiles escolares, libros de estudio, "
        "fotocopias, librería"
    )
    assert linea_libros in prompt_eg

    prompt_in = clasificador_concepto_service.construir_system_prompt(
        ctx_clf.db, "ingreso"
    )
    assert "- Trabajo (ingreso)" in prompt_in
    assert "(egreso)" not in prompt_in
    assert "- Alimentación" not in prompt_in
    assert "- Educación" not in prompt_in


def test_tc3_clasificar_concepto_uso_ia_y_cache(ctx_clf):
    """TC3: clasifica, registra uso_ia con costo > 0 y cachea al repetir."""
    ctx_clf.respuesta_cfg["opciones"] = [
        "Educación › Libros y útiles",
        "Ocio › Otros",
    ]
    pares, resp = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "fotocopias", "egreso"
    )
    assert pares == [("Educación", "Libros y útiles"), ("Ocio", None)]
    assert resp is not None
    assert len(ctx_clf.llamadas) == 1

    filas = ctx_clf.db.scalars(
        select(UsoIA).where(
            UsoIA.usuario_id == ctx_clf.usuario.id,
            UsoIA.funcion == "clasificar_concepto",
        )
    ).all()
    assert len(filas) == 1
    assert filas[0].costo_usd > Decimal("0")

    pares2, resp2 = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "Fotocopias ", "egreso"
    )
    assert pares2 == [("Educación", "Libros y útiles"), ("Ocio", None)]
    assert resp2 is None
    assert len(ctx_clf.llamadas) == 1


def test_tc4_filtra_fuera_de_enum_repetidas_y_limita_a_tres(ctx_clf):
    """TC4: descarta opciones fuera del enum, evita repetidas y toma hasta 3."""
    ctx_clf.respuesta_cfg["opciones"] = [
        "Inventada › Fantasma",
        "Educación › Libros y útiles",
        "Educación › Libros y útiles",
        "Ocio › Otros",
        "Hogar › Limpieza",
        "Salud › Farmacia",
    ]
    pares, _ = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "apuntes varios", "egreso"
    )
    assert pares == [
        ("Educación", "Libros y útiles"),
        ("Ocio", None),
        ("Hogar", "Limpieza"),
    ]


def test_tc5_textos_cortos_no_llaman_ia(ctx_clf):
    """TC5: textos normalizados con menos de 3 letras devuelven [] sin llamadas."""
    for txt in ("ab", "  "):
        pares, resp = clasificador_concepto_service.clasificar_concepto(
            ctx_clf.db, ctx_clf.usuario, txt, "egreso"
        )
        assert pares == []
        assert resp is None
    assert len(ctx_clf.llamadas) == 0


def test_tc6_tope_mensual_alcanzado_corta_ia(ctx_clf, monkeypatch):
    """TC6: con presupuesto mensual alcanzado devuelve [] y 0 llamadas."""
    monkeypatch.setattr(settings, "IA_PRESUPUESTO_MENSUAL_USD", 0.05)
    hoy = hoy_argentina()
    fecha_ar = datetime(hoy.year, hoy.month, hoy.day, 10, 0, 0, tzinfo=TZ_ARGENTINA)
    ctx_clf.db.add(
        UsoIA(
            id=uuid4(),
            fecha=fecha_ar,
            usuario_id=ctx_clf.usuario.id,
            funcion="texto_whatsapp",
            modelo="gpt-4o-mini",
            costo_usd=Decimal("0.050000"),
        )
    )
    ctx_clf.db.commit()

    pares, resp = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "imprimí apuntes", "egreso"
    )
    assert pares == []
    assert resp is None
    assert len(ctx_clf.llamadas) == 0


def test_tc7_tope_diario_60_y_ayer_no_cuenta(ctx_clf):
    """TC7: 60 filas hoy corta IA; 59 llama; filas de ayer no cuentan."""
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)
    fecha_hoy = datetime(hoy.year, hoy.month, hoy.day, 14, 0, 0, tzinfo=TZ_ARGENTINA)
    fecha_ayer = datetime(
        ayer.year, ayer.month, ayer.day, 14, 0, 0, tzinfo=TZ_ARGENTINA
    )

    filas_ayer = [
        UsoIA(
            id=uuid4(),
            fecha=fecha_ayer,
            usuario_id=ctx_clf.usuario.id,
            funcion="clasificar_concepto",
            modelo=settings.OPENAI_MODEL,
            costo_usd=Decimal("0.000100"),
        )
        for _ in range(10)
    ]
    filas_hoy = [
        UsoIA(
            id=uuid4(),
            fecha=fecha_hoy,
            usuario_id=ctx_clf.usuario.id,
            funcion="clasificar_concepto",
            modelo=settings.OPENAI_MODEL,
            costo_usd=Decimal("0.000100"),
        )
        for _ in range(59)
    ]
    ctx_clf.db.add_all(filas_ayer + filas_hoy)
    ctx_clf.db.commit()

    pares_59, _ = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "primer intento hoy", "egreso"
    )
    assert len(pares_59) == 1
    assert len(ctx_clf.llamadas) == 1

    pares_60, resp_60 = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "intento sesenta y uno", "egreso"
    )
    assert pares_60 == []
    assert resp_60 is None
    assert len(ctx_clf.llamadas) == 1


def test_tc8_error_no_se_guarda_en_cache(ctx_clf):
    """TC8: excepción en llamar_clasificador devuelve [] y reintenta luego."""
    ctx_clf.respuesta_cfg["error"] = RuntimeError("Error temporal de OpenAI")
    pares_err, resp_err = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "pedí sushi", "egreso"
    )
    assert pares_err == []
    assert resp_err is None
    assert len(ctx_clf.llamadas) == 1

    ctx_clf.respuesta_cfg["error"] = None
    ctx_clf.respuesta_cfg["opciones"] = ["Gastronomía › Delivery"]
    pares_ok, resp_ok = clasificador_concepto_service.clasificar_concepto(
        ctx_clf.db, ctx_clf.usuario, "pedí sushi", "egreso"
    )
    assert pares_ok == [("Gastronomía", "Delivery")]
    assert resp_ok is not None
    assert len(ctx_clf.llamadas) == 2


def test_tc9_parametros_schema_y_recorte_120_chars(ctx_clf):
    """TC9: model, temperature 0, schema estricto con enum y texto recortado a 120."""
    texto_largo = "  " + ("a" * 150) + "  "
    clasificador_concepto_service.clasificar_sin_limites(
        ctx_clf.db, texto_largo, "egreso"
    )
    assert len(ctx_clf.llamadas) == 1
    llamada = ctx_clf.llamadas[0]
    assert llamada["model"] == settings.OPENAI_MODEL
    assert llamada["temperature"] == 0
    assert llamada["max_tokens"] == 120

    schema = llamada["schema"]
    assert schema["type"] == "json_schema"
    js = schema["json_schema"]
    assert js["strict"] is True
    assert js["schema"]["additionalProperties"] is False
    assert js["schema"]["required"] == ["opciones"]
    enum_schema = js["schema"]["properties"]["opciones"]["items"]["enum"]
    assert enum_schema == clasificador_concepto_service.opciones_validas(
        ctx_clf.db, "egreso"
    )

    user_msg = llamada["mensajes"][1]["content"]
    assert user_msg == "a" * 120
    assert len(user_msg) == 120


def test_frases_evaluacion_clasificador_validas(ctx_clf):
    """F2: el JSON tiene 69 frases y cada esperado existe en opciones_validas."""
    ruta_json = (
        Path(__file__).resolve().parent.parent
        / "scripts"
        / "evaluacion"
        / "frases_clasificador.json"
    )
    datos = json.loads(ruta_json.read_text(encoding="utf-8"))
    assert len(datos) == 69

    opciones_por_tipo = {
        "egreso": set(clasificador_concepto_service.opciones_validas(ctx_clf.db, "egreso")),
        "ingreso": set(
            clasificador_concepto_service.opciones_validas(ctx_clf.db, "ingreso")
        ),
    }
    for item in datos:
        tipo = item["tipo"]
        assert tipo in opciones_por_tipo
        assert item["esperado"] in opciones_por_tipo[tipo]
    assert len(ctx_clf.llamadas) == 0
