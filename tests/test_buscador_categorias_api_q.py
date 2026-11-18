# -*- coding: utf-8 -*-
"""
tests/test_buscador_categorias_api_q.py — Pruebas API de /buscar y /sugerir.
"""
from __future__ import annotations

from datetime import datetime
import json
from types import SimpleNamespace
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.catalogo_categorias import ARBOL_CATEGORIAS
from app.core.database import Base, get_db
from app.main import app as fastapi_app
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.memoria_comercio import MemoriaComercio
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.usuario import (
    AuthProvider,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.services import categoria_service, clasificador_concepto_service
from app.services.memoria_comercio_service import clave_comercio
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
    fastapi_app.dependency_overrides.clear()
    categoria_service.invalidar_cache_categorias_globales()
    clasificador_concepto_service.limpiar_caches_clasificador()


@pytest.fixture(name="ctx_api", scope="function")
def ctx_api_fixture(monkeypatch):
    """Base SQLite en memoria con catálogo cargado, usuario y fake del clasificador."""
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
    usuario = _crear_usuario(db, "principal")

    llamadas: list[dict] = []
    opciones_fijas: list[str] = ["Educación › Libros y útiles"]

    def _fake_clasificador(
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
        contenido = json.dumps({"opciones": list(opciones_fijas)})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=contenido))],
            usage=SimpleNamespace(
                prompt_tokens=300,
                completion_tokens=20,
                prompt_tokens_details=None,
            ),
        )

    monkeypatch.setattr(
        "app.services.clasificador_concepto_service.llamar_clasificador",
        _fake_clasificador,
    )

    usuario_actual = {"u": usuario}
    fastapi_app.dependency_overrides[get_db] = lambda: db
    fastapi_app.dependency_overrides[get_current_user] = lambda: usuario_actual["u"]
    client = TestClient(fastapi_app)

    try:
        yield SimpleNamespace(
            db=db,
            usuario=usuario,
            usuario_actual=usuario_actual,
            client=client,
            llamadas=llamadas,
            opciones_fijas=opciones_fijas,
        )
    finally:
        fastapi_app.dependency_overrides.clear()
        db.close()


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
        apellido="Buscador",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _ids_cat_sub(
    db: Session,
    cat_nombre: str,
    sub_nombre: str,
    tipo: str = "egreso",
) -> tuple[str, str]:
    """Obtiene los UUIDs en texto de categoría y subcategoría de la base."""
    cat = db.scalar(
        select(Categoria).where(
            Categoria.nombre == cat_nombre,
            Categoria.tipo == TipoCategoria(tipo),
        )
    )
    assert cat is not None
    sub = db.scalar(
        select(Subcategoria).where(
            Subcategoria.categoria_id == cat.id,
            Subcategoria.nombre == sub_nombre,
        )
    )
    assert sub is not None
    return str(cat.id), str(sub.id)


def test_tq6_catalogo_ids_reales_sin_ia(ctx_api):
    """TQ6: pescadería devuelve Alimentación > Carnicería por catálogo y 0 llamadas IA."""
    cat_id, sub_id = _ids_cat_sub(ctx_api.db, "Alimentación", "Carnicería", "egreso")

    resp = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "pescadería", "tipo": "egreso"},
    )
    assert resp.status_code == 200
    resultados = resp.json()["resultados"]
    assert len(resultados) >= 1
    primero = resultados[0]
    assert primero["categoria"] == "Alimentación"
    assert primero["subcategoria"] == "Carnicería"
    assert primero["categoria_id"] == cat_id
    assert primero["subcategoria_id"] == sub_id
    assert primero["origen"] == "catalogo"
    assert len(ctx_api.llamadas) == 0


def test_tq7_memoria_antes_que_catalogo_sin_repetir_y_aislada(ctx_api):
    """TQ7: memoria del usuario va primero, no se repite desde catálogo y no mezcla usuarios."""
    cat_id, sub_id = _ids_cat_sub(ctx_api.db, "Alimentación", "Supermercado", "egreso")
    cat_ocio_id, sub_salidas_id = _ids_cat_sub(ctx_api.db, "Ocio", "Salidas", "egreso")
    otro = _crear_usuario(ctx_api.db, "otro")

    hoy = hoy_argentina()
    ahora_ar = datetime(hoy.year, hoy.month, hoy.day, 11, 0, 0, tzinfo=TZ_ARGENTINA)

    ctx_api.db.add(
        MemoriaComercio(
            id=uuid4(),
            usuario_id=ctx_api.usuario.id,
            clave="anonima",
            tipo="egreso",
            categoria_id=uuid.UUID(cat_id),
            subcategoria_id=uuid.UUID(sub_id),
            fecha_creacion=ahora_ar,
            fecha_actualizacion=ahora_ar,
        )
    )
    ctx_api.db.add(
        MemoriaComercio(
            id=uuid4(),
            usuario_id=otro.id,
            clave="anonima vip",
            tipo="egreso",
            categoria_id=uuid.UUID(cat_ocio_id),
            subcategoria_id=uuid.UUID(sub_salidas_id),
            fecha_creacion=ahora_ar,
            fecha_actualizacion=ahora_ar,
        )
    )
    ctx_api.db.commit()

    resp = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "anonima", "tipo": "egreso"},
    )
    assert resp.status_code == 200
    resultados = resp.json()["resultados"]
    assert len(resultados) == 1
    assert resultados[0]["categoria"] == "Alimentación"
    assert resultados[0]["subcategoria"] == "Supermercado"
    assert resultados[0]["origen"] == "memoria"
    assert len(ctx_api.llamadas) == 0

    ctx_api.db.add(
        MemoriaComercio(
            id=uuid4(),
            usuario_id=ctx_api.usuario.id,
            clave="supermercado",
            tipo="egreso",
            categoria_id=uuid.UUID(cat_id),
            subcategoria_id=uuid.UUID(sub_id),
            fecha_creacion=ahora_ar,
            fecha_actualizacion=ahora_ar,
        )
    )
    ctx_api.db.commit()
    resp_sup = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "supermercado", "tipo": "egreso"},
    )
    assert resp_sup.status_code == 200
    res_sup = resp_sup.json()["resultados"]
    assert res_sup[0]["origen"] == "memoria"
    assert res_sup[0]["subcategoria"] == "Supermercado"
    iguales = [
        r for r in res_sup if r["categoria_id"] == cat_id and r["subcategoria_id"] == sub_id
    ]
    assert len(iguales) == 1


def test_tq12_tipo_otro_y_q_vacio(ctx_api):
    """TQ12: tipo inválido o q vacío devuelven 200 con [] y sin llamar IA."""
    resp_tipo = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "pescadería", "tipo": "otro"},
    )
    assert resp_tipo.status_code == 200
    assert resp_tipo.json() == {"resultados": []}

    resp_vacio = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "", "tipo": "egreso"},
    )
    assert resp_vacio.status_code == 200
    assert resp_vacio.json() == {"resultados": []}
    assert len(ctx_api.llamadas) == 0


def test_tc10_sugerir_ia_ids_base(ctx_api):
    """TC10: /sugerir propone Educación > Libros y útiles con origen ia e ids reales."""
    cat_id, sub_id = _ids_cat_sub(ctx_api.db, "Educación", "Libros y útiles", "egreso")
    ctx_api.opciones_fijas[:] = ["Educación › Libros y útiles"]

    resp = ctx_api.client.get(
        "/categorias/sugerir",
        params={"q": "imprimí apuntes en la librería", "tipo": "egreso"},
    )
    assert resp.status_code == 200
    resultados = resp.json()["resultados"]
    assert len(resultados) == 1
    assert resultados[0] == {
        "categoria_id": cat_id,
        "subcategoria_id": sub_id,
        "categoria": "Educación",
        "subcategoria": "Libros y útiles",
        "origen": "ia",
    }
    assert len(ctx_api.llamadas) == 1


def test_tc11_sugerir_memoria_sin_ia_y_aislada(ctx_api):
    """TC11: /sugerir usa memoria exacta sin llamar a la IA y aísla usuarios."""
    cat_id, sub_id = _ids_cat_sub(ctx_api.db, "Alimentación", "Supermercado", "egreso")
    otro = _crear_usuario(ctx_api.db, "otro_sug")
    hoy = hoy_argentina()
    ahora_ar = datetime(hoy.year, hoy.month, hoy.day, 12, 0, 0, tzinfo=TZ_ARGENTINA)

    ctx_api.db.add(
        MemoriaComercio(
            id=uuid4(),
            usuario_id=ctx_api.usuario.id,
            clave=clave_comercio("la anonima") or "anonima",
            tipo="egreso",
            categoria_id=uuid.UUID(cat_id),
            subcategoria_id=uuid.UUID(sub_id),
            fecha_creacion=ahora_ar,
            fecha_actualizacion=ahora_ar,
        )
    )
    ctx_api.db.commit()

    resp = ctx_api.client.get(
        "/categorias/sugerir",
        params={"q": "La Anónima", "tipo": "egreso"},
    )
    assert resp.status_code == 200
    assert resp.json()["resultados"] == [
        {
            "categoria_id": cat_id,
            "subcategoria_id": sub_id,
            "categoria": "Alimentación",
            "subcategoria": "Supermercado",
            "origen": "memoria",
        }
    ]
    assert len(ctx_api.llamadas) == 0

    ctx_api.usuario_actual["u"] = otro
    resp_otro = ctx_api.client.get(
        "/categorias/sugerir",
        params={"q": "La Anónima", "tipo": "egreso"},
    )
    assert resp_otro.status_code == 200
    assert resp_otro.json()["resultados"][0]["origen"] == "ia"
    assert len(ctx_api.llamadas) == 1


def test_tc12_buscar_nunca_llama_ia(ctx_api):
    """TC12: /buscar con 'xyzzy' y 'fotocopias' nunca llama a la IA."""
    r1 = ctx_api.client.get("/categorias/buscar", params={"q": "xyzzy", "tipo": "egreso"})
    assert r1.status_code == 200
    assert r1.json() == {"resultados": []}

    r2 = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "fotocopias", "tipo": "egreso"},
    )
    assert r2.status_code == 200
    assert len(r2.json()["resultados"]) >= 1
    assert len(ctx_api.llamadas) == 0


def test_tc13_sugerir_tipo_invalido_y_q_corto(ctx_api):
    """TC13: /sugerir con tipo 'otro', q vacío o 'ab' devuelve [] sin llamar IA."""
    for params in (
        {"q": "imprimí apuntes", "tipo": "otro"},
        {"q": "", "tipo": "egreso"},
        {"q": "ab", "tipo": "egreso"},
    ):
        resp = ctx_api.client.get("/categorias/sugerir", params=params)
        assert resp.status_code == 200
        assert resp.json() == {"resultados": []}
    assert len(ctx_api.llamadas) == 0


def test_tc14_buscar_fotoc_con_y_sin_memorias(ctx_api):
    """TC14: /buscar 'fotoc' egreso da 200 y (Educación, Libros y útiles) primero."""
    cat_sup, sub_sup = _ids_cat_sub(ctx_api.db, "Alimentación", "Supermercado", "egreso")
    hoy = hoy_argentina()
    ahora_ar = datetime(hoy.year, hoy.month, hoy.day, 13, 0, 0, tzinfo=TZ_ARGENTINA)

    ctx_api.db.add(
        MemoriaComercio(
            id=uuid4(),
            usuario_id=ctx_api.usuario.id,
            clave="coto centro",
            tipo="egreso",
            categoria_id=uuid.UUID(cat_sup),
            subcategoria_id=uuid.UUID(sub_sup),
            fecha_creacion=ahora_ar,
            fecha_actualizacion=ahora_ar,
        )
    )
    ctx_api.db.commit()

    resp_con = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "fotoc", "tipo": "egreso"},
    )
    assert resp_con.status_code == 200
    res_con = resp_con.json()["resultados"]
    assert len(res_con) >= 1
    assert res_con[0]["categoria"] == "Educación"
    assert res_con[0]["subcategoria"] == "Libros y útiles"

    sin_mem = _crear_usuario(ctx_api.db, "sin_mem")
    ctx_api.usuario_actual["u"] = sin_mem
    resp_sin = ctx_api.client.get(
        "/categorias/buscar",
        params={"q": "fotoc", "tipo": "egreso"},
    )
    assert resp_sin.status_code == 200
    res_sin = resp_sin.json()["resultados"]
    assert len(res_sin) >= 1
    assert res_sin[0]["categoria"] == "Educación"
    assert res_sin[0]["subcategoria"] == "Libros y útiles"
    assert len(ctx_api.llamadas) == 0


def test_tc15_error_ia_en_sugerir_devuelve_200_vacio(ctx_api, monkeypatch):
    """TC15: un fallo del clasificador IA en /sugerir responde 200 con []."""
    def _falla(*args, **kwargs):
        raise RuntimeError("Fallo simulado del clasificador")

    monkeypatch.setattr(
        "app.services.clasificador_concepto_service.llamar_clasificador",
        _falla,
    )
    resp = ctx_api.client.get(
        "/categorias/sugerir",
        params={"q": "imprimí apuntes en la librería", "tipo": "egreso"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"resultados": []}
