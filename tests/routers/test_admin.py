import pytest
from uuid import uuid4
from fastapi import status, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

from app.main import app
from app.core.database import Base, get_db
from app.core.auth import get_current_admin_user
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda

# Override JSONB type compilation for SQLite so that the test schema constructs successfully
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

current_test_admin = None


def override_get_current_admin_user():
    if current_test_admin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado"
        )
    if not current_test_admin.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés permiso para hacer eso."
        )
    return current_test_admin


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_admin_user] = override_get_current_admin_user


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def admin_user(db_session):
    global current_test_admin
    admin = Usuario(
        id=uuid4(),
        nombre="Admin",
        apellido="Tester",
        email="admin@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.ADMIN,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=True,
    )
    db_session.add(admin)
    db_session.commit()
    current_test_admin = admin
    return admin


@pytest.fixture
def regular_user(db_session):
    user = Usuario(
        id=uuid4(),
        nombre="Regular",
        apellido="User",
        email="user@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=False,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_hacer_admin_y_quitar_admin(client, admin_user, regular_user, db_session):
    # 1. Promover regular_user a admin
    response = client.patch(
        f"/v1/admin/usuarios/{regular_user.id}/admin",
        json={"is_admin": True}
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["success"] is True
    assert data["data"]["is_admin"] is True

    # Verificar en DB
    db_session.expire_all()
    db_user = db_session.execute(select(Usuario).where(Usuario.id == regular_user.id)).scalar_one()
    assert db_user.is_admin is True
    assert db_user.rol == RolUsuario.ADMIN

    # 2. Revocar rol de admin
    response2 = client.patch(
        f"/v1/admin/usuarios/{regular_user.id}/admin",
        json={"is_admin": False}
    )
    assert response2.status_code == status.HTTP_200_OK
    data2 = response2.json()
    assert data2["success"] is True
    assert data2["data"]["is_admin"] is False

    db_session.expire_all()
    db_user2 = db_session.execute(select(Usuario).where(Usuario.id == regular_user.id)).scalar_one()
    assert db_user2.is_admin is False
    assert db_user2.rol == RolUsuario.USUARIO


def test_admin_no_puede_quitarse_permisos_a_si_mismo(client, admin_user):
    response = client.patch(
        f"/v1/admin/usuarios/{admin_user.id}/admin",
        json={"is_admin": False}
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["error"]["code"] == "ADMIN_CANNOT_DEMOTE_SELF"


def test_eliminar_usuario_exitoso(client, admin_user, regular_user, db_session):
    target_id = regular_user.id
    target_email = regular_user.email
    db_session.expunge(regular_user)

    # Eliminar con email de confirmación correcto
    response = client.request(
        "DELETE",
        f"/v1/admin/usuarios/{target_id}",
        json={"email_confirmacion": target_email}
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["success"] is True

    # Verificar que el usuario no exista más en DB
    deleted = db_session.execute(select(Usuario).where(Usuario.id == target_id)).scalar_one_or_none()
    assert deleted is None


def test_eliminar_usuario_rechazado_por_email_invalido(client, admin_user, regular_user, db_session):
    # Email de confirmación no coincide
    response = client.request(
        "DELETE",
        f"/v1/admin/usuarios/{regular_user.id}",
        json={"email_confirmacion": "otro_email@argentum.com"}
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["error"]["code"] == "EMAIL_MISMATCH"

    # Usuario sigue existiendo en DB
    db_session.expire_all()
    still_exists = db_session.execute(select(Usuario).where(Usuario.id == regular_user.id)).scalar_one_or_none()
    assert still_exists is not None


def test_admin_no_puede_autoeliminarse_desde_admin(client, admin_user, db_session):
    response = client.request(
        "DELETE",
        f"/v1/admin/usuarios/{admin_user.id}",
        json={"email_confirmacion": admin_user.email}
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["error"]["code"] == "ADMIN_CANNOT_DELETE_SELF"


def test_diseno_decisiones_admin_flow(client, admin_user, regular_user):
    global current_test_admin

    # 1. Sin autenticación -> 401
    current_test_admin = None
    res_unauth = client.get("/v1/admin/diseno/decisiones")
    assert res_unauth.status_code == status.HTTP_401_UNAUTHORIZED

    # 2. Usuario no admin -> 403
    current_test_admin = regular_user
    res_forbidden = client.get("/v1/admin/diseno/decisiones")
    assert res_forbidden.status_code == status.HTTP_403_FORBIDDEN

    # 3. Admin consulta y guarda decisión -> 200
    current_test_admin = admin_user
    res_get = client.get("/v1/admin/diseno/decisiones")
    assert res_get.status_code == status.HTTP_200_OK
    assert res_get.json()["success"] is True

    payload = {
        "categoryId": "selectores_segmentados",
        "preferredVariantId": "src/components/ui/SegmentedControl.tsx#L25_SegmentedControl",
        "preferredVariantName": "SegmentedControl (Shared UI)",
        "decisionState": "aprobada",
        "notes": "Unificar con border-radius 12px y elevación sutil",
        "equivalentComponents": ["TransaccionModal.segmentedBar", "PagarResumenModal.segmentedBar"],
        "customTokens": {"borderRadius": "12px", "padding": "4px"}
    }
    res_post = client.post("/v1/admin/diseno/decisiones", json=payload)
    assert res_post.status_code == status.HTTP_200_OK
    assert res_post.json()["data"]["decisionState"] == "aprobada"

    # 4. Guardar y consultar especificación -> 200
    spec_payload = {
        "familyId": "selectores_segmentados",
        "familyName": "Selectores Segmentados",
        "targetTokens": {"borderRadius": "12px", "height": "40px"},
        "stateGuidelines": {"hover": "translateY(-0.5px)", "active": "elevation-sm"},
        "accessibilityRules": ["role=radiogroup", "aria-checked"],
        "status": "aprobada",
        "notes": "Especificación oficial para todos los modales"
    }
    res_spec_post = client.post("/v1/admin/diseno/especificaciones", json=spec_payload)
    assert res_spec_post.status_code == status.HTTP_200_OK
    assert res_spec_post.json()["data"]["status"] == "aprobada"

    res_spec_get = client.get("/v1/admin/diseno/especificaciones")
    assert res_spec_get.status_code == status.HTTP_200_OK
    assert "selectores_segmentados" in res_spec_get.json()["data"]

    # 5. Control de concurrencia optimista y detección de conflicto 409
    first_save = client.post("/v1/admin/diseno/decisiones", json={
        "categoryId": "botones_accion",
        "preferredVariantName": "Button (Primary)",
        "decisionState": "revisada",
        "notes": "Admin A edita primero"
    })
    assert first_save.status_code == status.HTTP_200_OK
    saved_v1 = first_save.json()["data"]
    assert saved_v1["version"] >= 1

    # Admin B también guardó encima, incrementando la versión
    second_save = client.post("/v1/admin/diseno/decisiones", json={
        "categoryId": "botones_accion",
        "preferredVariantName": "Button (Primary V2)",
        "decisionState": "aprobada",
        "notes": "Admin B edita y avanza versión",
        "version": saved_v1["version"]
    })
    assert second_save.status_code == status.HTTP_200_OK
    saved_v2 = second_save.json()["data"]
    assert saved_v2["version"] == saved_v1["version"] + 1

    # Admin A intenta enviar cambios con la versión desactualizada original -> 409 Conflict
    conflict_save = client.post("/v1/admin/diseno/decisiones", json={
        "categoryId": "botones_accion",
        "preferredVariantName": "Button (Old State)",
        "decisionState": "pendiente",
        "notes": "Admin A intenta sobreescribir sin recargar",
        "version": saved_v1["version"]  # Versión anterior menor a saved_v2["version"]
    })
    assert conflict_save.status_code == status.HTTP_409_CONFLICT
    assert conflict_save.json()["error"]["code"] == "CONCURRENCY_CONFLICT"


