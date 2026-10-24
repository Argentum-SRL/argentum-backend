"""
Pruebas de validación de Modo PC (E.1 a E.8).
Ubicación: tests/test_entorno_pc.py
"""
import pytest
from app.core.config import Settings, settings
from app.core.entorno import opciones_conexion


@pytest.fixture(autouse=True)
def limpiar_entorno(monkeypatch):
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID", raising=False)
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.delenv("ARGENTUM_PRODUCCION_CONFIRMADA", raising=False)
    monkeypatch.delenv("ARGENTUM_PRODUCCION_SOLO_LECTURA", raising=False)


def test_e1_error_base_no_local_sin_confirmacion():
    # E.1 Sin RAILWAY_*, DATABASE_URL postgresql://u:p@db.ejemplo.com:5432/x y sin ARGENTUM_PRODUCCION_CONFIRMADA:
    # construir Settings da error que contiene "En esta PC la app usa solo la copia local".
    with pytest.raises(ValueError, match=r"En esta PC la app usa solo la copia local"):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql://u:p@db.ejemplo.com:5432/x",
            SECRET_KEY="test_secret",
        )


def test_e2_base_local_apaga_tokens_y_scheduler():
    # E.2 Sin RAILWAY_*, DATABASE_URL postgresql://postgres:x@localhost:5433/argentum_local,
    # WHATSAPP_ACCESS_TOKEN "t", RESEND_API_KEY "r", SMTP_PASSWORD "s", ENABLE_SCHEDULER True y OPENAI_API_KEY "o":
    # quedan "", "", "", False y "o"; ES_MODO_PC True.
    s = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://postgres:x@localhost:5433/argentum_local",
        SECRET_KEY="test_secret",
        WHATSAPP_ACCESS_TOKEN="t",
        RESEND_API_KEY="r",
        SMTP_PASSWORD="s",
        ENABLE_SCHEDULER=True,
        OPENAI_API_KEY="o",
    )
    assert s.WHATSAPP_ACCESS_TOKEN == ""
    assert s.RESEND_API_KEY == ""
    assert s.SMTP_PASSWORD == ""
    assert s.ENABLE_SCHEDULER is False
    assert s.OPENAI_API_KEY == "o"
    assert s.ES_MODO_PC is True


def test_e3_base_local_127_y_sqlite():
    # E.3 Lo mismo que E.2 con host 127.0.0.1 y con sqlite:///x.db: mismo resultado.
    for url in ("postgresql://postgres:x@127.0.0.1:5433/argentum_local", "sqlite:///x.db"):
        s = Settings(
            _env_file=None,
            DATABASE_URL=url,
            SECRET_KEY="test_secret",
            WHATSAPP_ACCESS_TOKEN="t",
            RESEND_API_KEY="r",
            SMTP_PASSWORD="s",
            ENABLE_SCHEDULER=True,
            OPENAI_API_KEY="o",
        )
        assert s.WHATSAPP_ACCESS_TOKEN == ""
        assert s.RESEND_API_KEY == ""
        assert s.SMTP_PASSWORD == ""
        assert s.ENABLE_SCHEDULER is False
        assert s.OPENAI_API_KEY == "o"
        assert s.ES_MODO_PC is True


def test_e4_produccion_confirmada():
    # E.4 Sin RAILWAY_*, la URL de E.1 y ARGENTUM_PRODUCCION_CONFIRMADA "1":
    # sin error; tokens vacíos, ENABLE_SCHEDULER False, ES_MODO_PC True.
    import os
    os.environ["ARGENTUM_PRODUCCION_CONFIRMADA"] = "1"
    try:
        s = Settings(
            _env_file=None,
            DATABASE_URL="postgresql://u:p@db.ejemplo.com:5432/x",
            SECRET_KEY="test_secret",
            WHATSAPP_ACCESS_TOKEN="t",
            RESEND_API_KEY="r",
            SMTP_PASSWORD="s",
            ENABLE_SCHEDULER=True,
        )
        assert s.WHATSAPP_ACCESS_TOKEN == ""
        assert s.RESEND_API_KEY == ""
        assert s.SMTP_PASSWORD == ""
        assert s.ENABLE_SCHEDULER is False
        assert s.ES_MODO_PC is True
    finally:
        os.environ.pop("ARGENTUM_PRODUCCION_CONFIRMADA", None)


def test_e5_servidor_railway(monkeypatch):
    # E.5 Con RAILWAY_ENVIRONMENT_ID "x" y la URL de E.1: los tokens y ENABLE_SCHEDULER quedan como vienen;
    # ES_MODO_PC False. Lo mismo con solo RAILWAY_PROJECT_ID.
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "x")
    s = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://u:p@db.ejemplo.com:5432/x",
        SECRET_KEY="test_secret",
        WHATSAPP_ACCESS_TOKEN="t",
        RESEND_API_KEY="r",
        SMTP_PASSWORD="s",
        ENABLE_SCHEDULER=True,
    )
    assert s.WHATSAPP_ACCESS_TOKEN == "t"
    assert s.RESEND_API_KEY == "r"
    assert s.SMTP_PASSWORD == "s"
    assert s.ENABLE_SCHEDULER is True
    assert s.ES_MODO_PC is False

    monkeypatch.delenv("RAILWAY_ENVIRONMENT_ID")
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    s2 = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://u:p@db.ejemplo.com:5432/x",
        SECRET_KEY="test_secret",
        WHATSAPP_ACCESS_TOKEN="t2",
        RESEND_API_KEY="r2",
        SMTP_PASSWORD="s2",
        ENABLE_SCHEDULER=True,
    )
    assert s2.WHATSAPP_ACCESS_TOKEN == "t2"
    assert s2.RESEND_API_KEY == "r2"
    assert s2.SMTP_PASSWORD == "s2"
    assert s2.ENABLE_SCHEDULER is True
    assert s2.ES_MODO_PC is False


def test_e6_confirmacion_invalida_e_inmutabilidad_es_modo_pc(monkeypatch):
    # E.6 ARGENTUM_PRODUCCION_CONFIRMADA "si" con la URL de E.1: el error de E.1.
    # ES_MODO_PC "False" en el entorno con la URL de E.2: ES_MODO_PC sigue True.
    monkeypatch.setenv("ARGENTUM_PRODUCCION_CONFIRMADA", "si")
    with pytest.raises(ValueError, match=r"En esta PC la app usa solo la copia local"):
        Settings(
            _env_file=None,
            DATABASE_URL="postgresql://u:p@db.ejemplo.com:5432/x",
            SECRET_KEY="test_secret",
        )

    monkeypatch.delenv("ARGENTUM_PRODUCCION_CONFIRMADA", raising=False)
    monkeypatch.setenv("ES_MODO_PC", "False")
    s = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://postgres:x@localhost:5433/argentum_local",
        SECRET_KEY="test_secret",
    )
    assert s.ES_MODO_PC is True


def test_e7_envios_simulados_sin_red(monkeypatch):
    # E.7 Con WHATSAPP_ACCESS_TOKEN vacío, whatsapp_service._post_meta devuelve ok True,
    # motivo "sin_configuracion" e intentos 0, y el cliente HTTP de Meta no se llama (un mock que falla si se lo llama);
    # con RESEND_API_KEY vacío, email_service._enviar_email devuelve True sin llamar a httpx.post.
    from app.services import whatsapp_service
    from app.services import email_service

    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
    monkeypatch.setattr(whatsapp_service.settings, "WHATSAPP_ACCESS_TOKEN", "")

    def mock_meta_client_fail():
        pytest.fail("get_meta_http_client no debe llamarse con token vacío")

    monkeypatch.setattr(whatsapp_service, "get_meta_http_client", mock_meta_client_fail)
    res_wpp = whatsapp_service._post_meta({"to": "123"})
    assert res_wpp.ok is True
    assert res_wpp.motivo == "sin_configuracion"
    assert res_wpp.intentos == 0

    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "")

    def mock_httpx_post_fail(*args, **kwargs):
        pytest.fail("httpx.post no debe llamarse con RESEND_API_KEY vacía")

    monkeypatch.setattr(email_service.httpx, "post", mock_httpx_post_fail)
    res_email = email_service._enviar_email("test@example.com", "Asunto", "Cuerpo")
    assert res_email is True


def test_e8_opciones_conexion():
    # E.8 opciones_conexion: URL postgresql y entorno vacío → {"options": "-csearch_path=public"};
    # con ARGENTUM_PRODUCCION_SOLO_LECTURA "1" → options contiene "-csearch_path=public" y "default_transaction_read_only=on";
    # sqlite → {}.
    res_pg = opciones_conexion("postgresql://u:p@db:5432/x", {})
    assert res_pg == {"options": "-csearch_path=public"}

    res_ro = opciones_conexion(
        "postgresql://u:p@db:5432/x",
        {"ARGENTUM_PRODUCCION_SOLO_LECTURA": "1"},
    )
    assert "-csearch_path=public" in res_ro.get("options", "")
    assert "default_transaction_read_only=on" in res_ro.get("options", "")

    res_sqlite = opciones_conexion(
        "sqlite:///test.db",
        {"ARGENTUM_PRODUCCION_SOLO_LECTURA": "1"},
    )
    assert res_sqlite == {}
