import pytest
from datetime import date, timedelta
from uuid import uuid4
from fastapi import status, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

# Override JSONB type compilation for SQLite so that the test schema constructs successfully
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.main import app
from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda

from sqlalchemy.pool import StaticPool

# Database setup for testing
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Reference to the current authenticated user in tests
current_test_user = None

def override_get_current_user():
    if current_test_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado"
        )
    return current_test_user

@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    """Inicializa la base de datos de prueba."""
    import app.models
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(name="client", scope="function")
def client_fixture(db_session):
    """Configura el TestClient y los overrides de dependencias."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    
    client = TestClient(app)
    yield client
    
    app.dependency_overrides.clear()

def test_post_datos_personales_menor_de_edad(client, db_session):
    """Verifica que el registro de un usuario menor de 18 años sea rechazado."""
    global current_test_user
    user = Usuario(
        id=uuid4(),
        nombre="",
        apellido="",
        email="test@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        onboarding_completo=False
    )
    db_session.add(user)
    db_session.commit()
    
    current_test_user = user
    
    # 17 años
    fecha_menor = date.today() - timedelta(days=17*365 + 10)
    
    response = client.post(
        "/onboarding/datos-personales",
        json={
            "nombre": "Menor",
            "apellido": "User",
            "fecha_nacimiento": str(fecha_menor),
            "sexo": "no_binario"
        }
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["message"] == "Tenés que ser mayor de 18 años para crear una cuenta en Argentum"

def test_post_datos_personales_mayor_de_edad(client, db_session):
    """Verifica que el registro de un usuario de 18+ años sea exitoso."""
    global current_test_user
    user = Usuario(
        id=uuid4(),
        nombre="",
        apellido="",
        email="test2@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        onboarding_completo=False
    )
    db_session.add(user)
    db_session.commit()
    
    current_test_user = user
    
    # 19 años
    fecha_mayor = date.today() - timedelta(days=19*365)
    
    response = client.post(
        "/onboarding/datos-personales",
        json={
            "nombre": "Mayor",
            "apellido": "User",
            "fecha_nacimiento": str(fecha_mayor),
            "sexo": "masculino"
        }
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["completado"] is True
