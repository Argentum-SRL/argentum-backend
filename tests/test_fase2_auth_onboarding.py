import pytest
from uuid import uuid4
from unittest.mock import patch
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool

# Compilar JSONB como TEXT para SQLite en memoria
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.main import app
from app.core.database import Base, get_db
from app.core.security import normalizar_email, validar_reglas_password, verify_password, get_password_hash, pwd_context
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda

# Configuración de base de datos aislada en memoria
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(name="client", scope="function")
def client_fixture(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _get_error_message(res) -> str:
    data = res.json()
    if isinstance(data, dict):
        if "error" in data and isinstance(data["error"], dict):
            return data["error"].get("message", "")
        return data.get("detail", "")
    return str(data)


# ── Escenario 1: Google -> Definir Password -> Login Tradicional ─────────────
def test_google_register_definir_password_login_tradicional(client, db_session):
    mock_token_info = {
        "sub": "google-uid-12345",
        "email": "google_user@argentum.app",
        "name": "Google User",
        "picture": "https://lh3.googleusercontent.com/a/photo",
        "email_verified": True,
    }

    with patch("app.routers.auth.verify_google_token", return_value=mock_token_info), \
         patch("app.services.email_service.enviar_email_bienvenida"), \
         patch("app.services.notificacion_email_service.enviar_email_notificacion"):
        res = client.post("/auth/google", json={"token": "valid_google_token"})
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        token = data["access_token"]
        assert token is not None

        # Definir contraseña nueva (sin password actual porque viene de Google)
        res_pwd = client.put(
            "/usuarios/me/password",
            json={
                "password_nueva": "ContrasenaSegura123!",
                "password_nueva_confirmacion": "ContrasenaSegura123!",
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res_pwd.status_code == status.HTTP_200_OK

        # Login tradicional con el mismo email y contraseña recién definida
        res_login = client.post(
            "/auth/login",
            json={"email": "google_user@argentum.app", "password": "ContrasenaSegura123!"}
        )
        assert res_login.status_code == status.HTTP_200_OK
        login_data = res_login.json()
        assert "access_token" in login_data
        assert login_data["usuario"]["email"] == "google_user@argentum.app"


# ── Escenario 2: Tradicional y luego Google con el mismo email (vincula, no duplica) ──
def test_tradicional_luego_google_mismo_email_vincula_no_duplica(client, db_session):
    email = "vinculacion@argentum.app"
    # 1. Crear usuario tradicional
    user = Usuario(
        id=uuid4(),
        email=email,
        password_hash=get_password_hash("PasswordTradicional123!"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        email_verificado=True,
        nombre="Usuario",
        apellido="Tradicional",
        moneda_principal=Moneda.ARS,
        estado=EstadoUsuario.ACTIVO,
        rol=RolUsuario.USUARIO,
    )
    db_session.add(user)
    db_session.commit()

    initial_id = user.id

    # 2. Login con Google con el mismo email
    mock_token_info = {
        "sub": "google-vinculado-999",
        "email": email,
        "name": "Usuario Google",
        "picture": "https://avatar.com/pic",
        "email_verified": True,
    }

    with patch("app.routers.auth.verify_google_token", return_value=mock_token_info):
        res = client.post("/auth/google", json={"token": "valid_token"})
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["usuario"]["id"] == str(initial_id)

    # Verificar que no hay duplicados en la base
    users = db_session.execute(select(Usuario).where(Usuario.email == email)).scalars().all()
    assert len(users) == 1
    assert users[0].auth_provider == AuthProvider.EMAIL


# ── Escenario 3: Email con mayúsculas y espacios (normalización unificada) ────
def test_email_capitalizacion_y_espacios_normalizados(client, db_session):
    email_con_espacios = "   User.Capitalized@Example.COM   "
    email_esperado = "user.capitalized@example.com"

    with patch("app.services.email_service.enviar_email_verificacion"), \
         patch("app.routers.auth.verificar_turnstile_token", return_value=True):
        res = client.post(
            "/auth/register",
            json={
                "email": email_con_espacios,
                "password": "Password123!",
                "nombre": "Test",
                "apellido": "User",
                "turnstile_token": "mock_valid_token",
            }
        )
        assert res.status_code == status.HTTP_201_CREATED

    # Verificar en base de datos que quedó normalizado en minúsculas y sin espacios
    user_db = db_session.execute(select(Usuario).where(Usuario.email == email_esperado)).scalar_one_or_none()
    assert user_db is not None
    assert user_db.email == email_esperado

    # Marcar email verificado para login
    user_db.email_verificado = True
    db_session.commit()

    # Login con otra capitalización y espacios
    res_login = client.post(
        "/auth/login",
        json={"email": "  USER.CAPITALIZED@EXAMPLE.COM  ", "password": "Password123!"}
    )
    assert res_login.status_code == status.HTTP_200_OK
    assert res_login.json()["usuario"]["email"] == email_esperado


# ── Escenario 4: Google OAuth con email_verified False (rechazado con 400) ───
def test_google_token_email_verified_false_rechazado(client, db_session):
    mock_token_info = {
        "sub": "google-unverified-111",
        "email": "unverified@argentum.app",
        "name": "No Verificado",
        "email_verified": False,
    }

    with patch("app.routers.auth.verify_google_token", return_value=mock_token_info):
        res = client.post("/auth/google", json={"token": "unverified_token"})
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        error_detail = _get_error_message(res)
        assert "no está verificado" in error_detail.lower()

    # Verificar que no se creó usuario en la base
    user_db = db_session.execute(
        select(Usuario).where(Usuario.email == "unverified@argentum.app")
    ).scalar_one_or_none()
    assert user_db is None


# ── Escenario 5: Definir contraseña sin sesión o con contraseña existente sin actual ──
def test_definir_password_sin_sesion_o_existente_sin_actual(client, db_session):
    # 1. Sin sesión -> 401
    res_no_auth = client.put(
        "/usuarios/me/password",
        json={
            "password_nueva": "NuevaClave123!",
            "password_nueva_confirmacion": "NuevaClave123!",
        }
    )
    assert res_no_auth.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Con usuario que ya tiene password_hash, intentando cambiarla sin password_actual -> 400
    user = Usuario(
        id=uuid4(),
        email="has_pwd@argentum.app",
        password_hash=get_password_hash("ViejaClave123!"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        email_verificado=True,
        nombre="Has",
        apellido="Pwd",
        moneda_principal=Moneda.ARS,
        estado=EstadoUsuario.ACTIVO,
        rol=RolUsuario.USUARIO,
    )
    db_session.add(user)
    db_session.commit()

    # Login para obtener token
    res_login = client.post(
        "/auth/login",
        json={"email": "has_pwd@argentum.app", "password": "ViejaClave123!"}
    )
    token = res_login.json()["access_token"]

    # Intentar cambiar contraseña sin proveer password_actual
    res_change_fail = client.put(
        "/usuarios/me/password",
        json={
            "password_nueva": "NuevaClave123!",
            "password_nueva_confirmacion": "NuevaClave123!",
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_change_fail.status_code == status.HTTP_400_BAD_REQUEST
    error_msg = _get_error_message(res_change_fail)
    assert "actual" in error_msg.lower()


# ── Escenario 6: Contraseñas 72 bytes, 73 bytes, 128 chars y verify_password seguro ───
def test_passwords_longitud_bytes_utf8_y_verify_seguro():
    # 72 bytes exactos UTF-8 -> pasa (incluye mayúscula, minúscula, número y símbolo)
    pwd_72 = "Ab" + "a" * 68 + "1!"
    assert len(pwd_72.encode("utf-8")) == 72
    validar_reglas_password(pwd_72)  # No levanta excepción

    # 73 bytes exactos UTF-8 -> ValueError
    pwd_73 = "Ab" + "a" * 69 + "1!"
    assert len(pwd_73.encode("utf-8")) == 73
    with pytest.raises(ValueError) as exc_info:
        validar_reglas_password(pwd_73)
    assert "72 bytes" in str(exc_info.value)

    # 128 caracteres -> ValueError
    pwd_128 = "Ab" + "a" * 124 + "1!"
    assert len(pwd_128) == 128
    with pytest.raises(ValueError) as exc_128:
        validar_reglas_password(pwd_128)
    assert "72 bytes" in str(exc_128.value)

    # verify_password con contraseña > 72 bytes devuelve False sin romper con 500
    hashed = get_password_hash("PasswordValida123!")
    assert verify_password("A" * 100, hashed) is False
    assert verify_password(pwd_73, hashed) is False
    assert verify_password("PasswordValida123!", hashed) is True


# ── Escenario 7: Emails con guión bajo sin colisión por comodín SQL ilike ────
def test_email_con_guion_bajo_sin_falso_match_comodin(client, db_session):
    # En SQL LIKE, '_' matchea cualquier carácter único si no se escapa.
    # Con func.lower(Usuario.email) == normalizar_email(email), debe ser match exacto.
    u1 = Usuario(
        id=uuid4(),
        email="test_user@argentum.app",
        password_hash=get_password_hash("Pass123!"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        email_verificado=True,
        nombre="Test",
        apellido="Underscore",
        moneda_principal=Moneda.ARS,
        estado=EstadoUsuario.ACTIVO,
        rol=RolUsuario.USUARIO,
    )
    u2 = Usuario(
        id=uuid4(),
        email="testauser@argentum.app",
        password_hash=get_password_hash("Pass456!"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        email_verificado=True,
        nombre="Test",
        apellido="AChar",
        moneda_principal=Moneda.ARS,
        estado=EstadoUsuario.ACTIVO,
        rol=RolUsuario.USUARIO,
    )
    db_session.add_all([u1, u2])
    db_session.commit()

    # Login como test_user@argentum.app
    res1 = client.post(
        "/auth/login",
        json={"email": "test_user@argentum.app", "password": "Pass123!"}
    )
    assert res1.status_code == status.HTTP_200_OK
    assert res1.json()["usuario"]["email"] == "test_user@argentum.app"

    # Login como testauser@argentum.app
    res2 = client.post(
        "/auth/login",
        json={"email": "testauser@argentum.app", "password": "Pass456!"}
    )
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json()["usuario"]["email"] == "testauser@argentum.app"


# ── Escenario 8: Verificación email tradicional -> requiere_verificacion_telefono: False ──
def test_email_verificado_tradicional_requiere_telefono_false(client, db_session):
    email = "verif_tradicional@argentum.app"
    user = Usuario(
        id=uuid4(),
        email=email,
        password_hash=get_password_hash("Password123!"),
        password_configurada=True,
        auth_provider=AuthProvider.EMAIL,
        email_verificado=False,
        nombre="Verif",
        apellido="User",
        moneda_principal=Moneda.ARS,
        estado=EstadoUsuario.ACTIVO,
        rol=RolUsuario.USUARIO,
    )
    db_session.add(user)
    db_session.commit()

    codigo_str = "123456"
    with patch("app.routers.auth.verificar_codigo_email", return_value=(True, None)), \
         patch("app.services.email_service.enviar_email_bienvenida"):
        res = client.post(
            "/auth/email/verificar",
            json={"email": email, "codigo": codigo_str}
        )
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["usuario"]["email_verificado"] is True
        assert data["requiere_verificacion_telefono"] is False


# ── Escenario 9: Truncamiento histórico a 72 bytes en verify_password ────────
def test_verify_password_contrasena_larga_truncamiento_historico():
    pwd_80 = "A" * 70 + "B" * 10
    try:
        hashed = pwd_context.hash(pwd_80)
    except ValueError:
        hashed = pwd_context.hash(pwd_80[:72])

    assert verify_password(pwd_80, hashed) is True
    assert verify_password("otraClave123", hashed) is False

