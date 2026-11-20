# -*- coding: utf-8 -*-
"""
Pruebas de reautenticación del administrador (2B) para acciones delicadas.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from fastapi.testclient import TestClient
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

import app.models  # noqa: F401
from app.core.auth import get_current_admin_user
from app.core.database import Base, get_db
from app.core.reglas_texto_web import problemas_texto_web
from app.core.security import get_password_hash
from app.main import app
from app.models.accion_admin import AccionAdmin
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import reautenticacion_service

AHORA = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_sqlite(monkeypatch):
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSessionLocal)

    def _override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setitem(app.dependency_overrides, get_db, _override_db)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _crear_usuario(
    db: Session,
    *,
    email: str,
    is_admin: bool = False,
    password: str | None = None,
    auth_provider: AuthProvider = AuthProvider.EMAIL,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        nombre="Admin" if is_admin else "Regular",
        apellido="Test",
        email=email,
        auth_provider=auth_provider,
        rol=RolUsuario.ADMIN if is_admin else RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=is_admin,
        password_hash=get_password_hash(password) if password else None,
        password_configurada=bool(password),
        onboarding_completo=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def test_a1_a5_reautenticacion_password(db_session: Session, client: TestClient, monkeypatch, caplog):
    admin = _crear_usuario(db_session, email="admin@prueba.com", is_admin=True, password="ClaveAdmin123")
    target = _crear_usuario(db_session, email="user@prueba.com")
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    with caplog.at_level(logging.DEBUG):
        # 1. Sin password_actual -> 403 REAUTH_REQUERIDA y 0 filas de registro
        r1 = client.patch(f"/v1/admin/usuarios/{target.id}/admin", json={"is_admin": True})
        assert r1.status_code == status.HTTP_403_FORBIDDEN
        assert r1.json()["error"]["code"] == "REAUTH_REQUERIDA"
        assert r1.json()["error"]["message"] == "Para hacer esto, confirmá que sos vos."
        assert db_session.scalars(select(AccionAdmin)).all() == []

        # 2. Con google_credential y sin password_actual -> sigue pidiendo contraseña
        r_g = client.patch(
            f"/v1/admin/usuarios/{target.id}/admin",
            json={"is_admin": True, "google_credential": "tok_ignorado_123"},
        )
        assert r_g.status_code == status.HTTP_403_FORBIDDEN
        assert r_g.json()["error"]["code"] == "REAUTH_REQUERIDA"

        # 3. Contraseña incorrecta -> 403 REAUTH_INVALIDA y 1 fila verificacion_fallida
        r2 = client.patch(
            f"/v1/admin/usuarios/{target.id}/admin",
            json={"is_admin": True, "password_actual": "otra_clave_mal"},
        )
        assert r2.status_code == status.HTTP_403_FORBIDDEN
        assert r2.json()["error"]["code"] == "REAUTH_INVALIDA"
        assert r2.json()["error"]["message"] == "La contraseña no es correcta."

        filas_falla = db_session.scalars(select(AccionAdmin)).all()
        assert len(filas_falla) == 1
        assert filas_falla[0].accion == "verificacion_fallida"
        assert filas_falla[0].detalle == "Contraseña"

        # 4. Contraseña correcta -> 200 y fila hacer_admin
        r3 = client.patch(
            f"/v1/admin/usuarios/{target.id}/admin",
            json={"is_admin": True, "password_actual": "ClaveAdmin123"},
        )
        assert r3.status_code == status.HTTP_200_OK
        filas = db_session.scalars(select(AccionAdmin).order_by(AccionAdmin.fecha.asc(), AccionAdmin.id.asc())).all()
        assert [f.accion for f in filas] == ["verificacion_fallida", "hacer_admin"]

    assert "ClaveAdmin123" not in caplog.text
    assert "otra_clave_mal" not in caplog.text
    assert "tok_ignorado_123" not in caplog.text


def test_a2_bloqueo_por_fallas_recientes(db_session: Session):
    admin = _crear_usuario(db_session, email="admin@prueba.com", is_admin=True, password="ClaveAdmin123")

    # 5 fallas hace 16 minutos -> pasa
    for m in range(16, 21):
        db_session.add(
            AccionAdmin(
                id=uuid4(),
                fecha=AHORA - timedelta(minutes=m),
                admin_id=admin.id,
                admin_nombre="Admin",
                accion="verificacion_fallida",
                detalle="Contraseña",
            )
        )
    db_session.commit()
    reautenticacion_service.verificar_identidad_admin(db_session, admin, "ClaveAdmin123", None, AHORA)

    # 4 fallas recientes -> pasa
    for m in (1, 4, 8, 14):
        db_session.add(
            AccionAdmin(
                id=uuid4(),
                fecha=AHORA - timedelta(minutes=m),
                admin_id=admin.id,
                admin_nombre="Admin",
                accion="verificacion_fallida",
                detalle="Contraseña",
            )
        )
    db_session.commit()
    reautenticacion_service.verificar_identidad_admin(db_session, admin, "ClaveAdmin123", None, AHORA)

    # 5ta falla reciente -> 429 REAUTH_BLOQUEADA aunque la contraseña sea correcta
    db_session.add(
        AccionAdmin(
            id=uuid4(),
            fecha=AHORA - timedelta(minutes=10),
            admin_id=admin.id,
            admin_nombre="Admin",
            accion="verificacion_fallida",
            detalle="Contraseña",
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        reautenticacion_service.verificar_identidad_admin(db_session, admin, "ClaveAdmin123", None, AHORA)
    assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert exc.value.detail["error"]["code"] == "REAUTH_BLOQUEADA"
    assert exc.value.detail["error"]["message"] == "Probaste muchas veces. Esperá 15 minutos y probá de nuevo."


def test_a3_a5_reautenticacion_google(db_session: Session, monkeypatch, caplog):
    admin = _crear_usuario(
        db_session,
        email="g@prueba.com",
        is_admin=True,
        password=None,
        auth_provider=AuthProvider.GOOGLE,
    )

    with caplog.at_level(logging.DEBUG):
        # 1. Sin credencial -> 403 REAUTH_REQUERIDA
        with pytest.raises(HTTPException) as exc_req:
            reautenticacion_service.verificar_identidad_admin(db_session, admin, None, None, AHORA)
        assert exc_req.value.status_code == 403
        assert exc_req.value.detail["error"]["code"] == "REAUTH_REQUERIDA"

        # 2. Email coincidente sin importar mayúsculas + iat reciente -> pasa
        monkeypatch.setattr(
            "app.services.reautenticacion_service.auth_service.verify_google_token",
            lambda tok: {
                "email": "G@Prueba.com",
                "email_verified": True,
                "iat": int((AHORA - timedelta(minutes=1)).timestamp()),
            },
        )
        reautenticacion_service.verificar_identidad_admin(db_session, admin, None, "tok_google_ok_secreto", AHORA)

        # 3. Email distinto -> 403 REAUTH_INVALIDA + fila Google
        monkeypatch.setattr(
            "app.services.reautenticacion_service.auth_service.verify_google_token",
            lambda tok: {
                "email": "otro@prueba.com",
                "email_verified": True,
                "iat": int((AHORA - timedelta(minutes=1)).timestamp()),
            },
        )
        with pytest.raises(HTTPException) as exc_otro:
            reautenticacion_service.verificar_identidad_admin(db_session, admin, None, "tok_google_otro", AHORA)
        assert exc_otro.value.status_code == 403
        assert exc_otro.value.detail["error"]["code"] == "REAUTH_INVALIDA"
        assert exc_otro.value.detail["error"]["message"] == "Elegí la misma cuenta de Google con la que entrás."

        # 4. email_verified False -> 403
        monkeypatch.setattr(
            "app.services.reautenticacion_service.auth_service.verify_google_token",
            lambda tok: {
                "email": "g@prueba.com",
                "email_verified": False,
                "iat": int((AHORA - timedelta(minutes=1)).timestamp()),
            },
        )
        with pytest.raises(HTTPException) as exc_unver:
            reautenticacion_service.verificar_identidad_admin(db_session, admin, None, "tok_google_unver", AHORA)
        assert exc_unver.value.status_code == 403

        # 5. iat viejo (-6 min) -> 403
        monkeypatch.setattr(
            "app.services.reautenticacion_service.auth_service.verify_google_token",
            lambda tok: {
                "email": "g@prueba.com",
                "email_verified": True,
                "iat": int((AHORA - timedelta(minutes=6)).timestamp()),
            },
        )
        with pytest.raises(HTTPException) as exc_iat:
            reautenticacion_service.verificar_identidad_admin(db_session, admin, None, "tok_google_viejo", AHORA)
        assert exc_iat.value.status_code == 403

        # 6. verify_google_token lanza HTTPException 400 -> 403
        def _fail(tok):
            raise HTTPException(status_code=400, detail="Token malo")

        monkeypatch.setattr("app.services.reautenticacion_service.auth_service.verify_google_token", _fail)
        with pytest.raises(HTTPException) as exc_err:
            reautenticacion_service.verificar_identidad_admin(db_session, admin, None, "tok_google_err", AHORA)
        assert exc_err.value.status_code == 403

    filas = db_session.scalars(select(AccionAdmin)).all()
    assert len(filas) == 4
    assert all(f.accion == "verificacion_fallida" and f.detalle == "Google" for f in filas)
    for tok in ("tok_google_ok_secreto", "tok_google_otro", "tok_google_unver", "tok_google_viejo", "tok_google_err"):
        assert tok not in caplog.text


def test_a4_delete_orden_validaciones(db_session: Session, client: TestClient, monkeypatch):
    admin = _crear_usuario(db_session, email="admin@prueba.com", is_admin=True, password="ClaveAdmin123")
    target = _crear_usuario(db_session, email="target@prueba.com")
    monkeypatch.setitem(app.dependency_overrides, get_current_admin_user, lambda: admin)

    # 1. A uno mismo -> 400 ADMIN_CANNOT_DELETE_SELF
    r_self = client.request("DELETE", f"/v1/admin/usuarios/{admin.id}", json={"email_confirmacion": admin.email})
    assert r_self.status_code == 400
    assert r_self.json()["error"]["code"] == "ADMIN_CANNOT_DELETE_SELF"

    # 2. Email mal sin contraseña -> 400 EMAIL_MISMATCH (antes de reauth)
    r_bad_mail = client.request("DELETE", f"/v1/admin/usuarios/{target.id}", json={"email_confirmacion": "otro@prueba.com"})
    assert r_bad_mail.status_code == 400
    assert r_bad_mail.json()["error"]["code"] == "EMAIL_MISMATCH"

    # 3. Email bien sin contraseña -> 403 REAUTH_REQUERIDA y la cuenta sigue
    r_no_pass = client.request("DELETE", f"/v1/admin/usuarios/{target.id}", json={"email_confirmacion": target.email})
    assert r_no_pass.status_code == 403
    assert r_no_pass.json()["error"]["code"] == "REAUTH_REQUERIDA"
    assert db_session.scalar(select(Usuario).where(Usuario.id == target.id)) is not None

    # 4. Email bien y contraseña bien -> 200
    r_ok = client.request(
        "DELETE",
        f"/v1/admin/usuarios/{target.id}",
        json={"email_confirmacion": target.email, "password_actual": "ClaveAdmin123"},
    )
    assert r_ok.status_code == 200
    assert db_session.scalar(select(Usuario).where(Usuario.id == target.id)) is None


def test_a6_textos_reauth_cumplen_reglas_web():
    for txt in (
        reautenticacion_service.TEXTO_REQUERIDA,
        reautenticacion_service.TEXTO_CLAVE_MAL,
        reautenticacion_service.TEXTO_GOOGLE_MAL,
        reautenticacion_service.TEXTO_BLOQUEO,
    ):
        assert problemas_texto_web(txt) == []
