import pytest
from datetime import date
from uuid import uuid4
from decimal import Decimal
from fastapi import status, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool

# Override JSONB type compilation for SQLite in tests
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.main import app
from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda
from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate

# In-memory SQLite database setup
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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

@pytest.fixture(name="setup_data")
def setup_data_fixture(db_session):
    global current_test_user
    user = Usuario(
        id=uuid4(),
        email="test_tx@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal=Moneda.ARS,
        onboarding_completo=True,
    )
    db_session.add(user)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=user.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_actual=10000,
        saldo_inicial=10000,
        es_principal=True,
        es_efectivo=False,
    )
    db_session.add(billetera)

    categoria = Categoria(
        id=uuid4(),
        nombre="Alimentación",
        tipo=TipoTransaccion.EGRESO,
        es_global=True,
    )
    db_session.add(categoria)

    subcategoria = Subcategoria(
        id=uuid4(),
        categoria_id=categoria.id,
        nombre="Supermercado",
        es_global=True,
    )
    db_session.add(subcategoria)

    db_session.commit()
    current_test_user = user

    return {
        "user": user,
        "billetera": billetera,
        "categoria": categoria,
        "subcategoria": subcategoria,
    }


# ==============================================================================
# Tests de Schemas Pydantic
# ==============================================================================

def test_transaccion_create_schema_sin_descripcion():
    """Confirma que TransaccionCreate permite crear una transacción sin descripción."""
    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1500.50"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 27),
        categoria_id=uuid4(),
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=uuid4(),
        origen=OrigenTransaccion.MANUAL,
    )
    assert tx_data.descripcion == ""
    assert tx_data.monto == Decimal("1500.50")


def test_transaccion_create_schema_con_descripcion_espacios():
    """Confirma que TransaccionCreate hace strip a espacios en blanco en la descripción."""
    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 27),
        descripcion="   ",
        categoria_id=uuid4(),
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=uuid4(),
        origen=OrigenTransaccion.MANUAL,
    )
    assert tx_data.descripcion == ""


def test_transaccion_update_schema_sin_descripcion():
    """Confirma que TransaccionUpdate permite descripción None o vacía."""
    tx_update = TransaccionUpdate(monto=Decimal("2000.00"), descripcion="")
    assert tx_update.descripcion == ""

    tx_update_none = TransaccionUpdate(monto=Decimal("2000.00"))
    assert tx_update_none.descripcion is None


# ==============================================================================
# Tests de Endpoints FastAPI (/transacciones)
# ==============================================================================

def test_crear_transaccion_sin_descripcion_exitoso(client, setup_data):
    """Prueba que POST /transacciones permita crear una transacción omitiendo la descripción."""
    billetera = setup_data["billetera"]
    categoria = setup_data["categoria"]
    subcategoria = setup_data["subcategoria"]

    payload = {
        "tipo": "egreso",
        "monto": 2500.00,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "subcategoria_id": str(subcategoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    }

    response = client.post("/transacciones", json=payload)
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["descripcion"] == ""
    assert float(data["monto"]) == 2500.00
    assert data["tipo"] == "egreso"


def test_crear_transaccion_descripcion_vacia_exitoso(client, setup_data):
    """Prueba que POST /transacciones permita crear una transacción con descripcion=""."""
    billetera = setup_data["billetera"]
    categoria = setup_data["categoria"]

    payload = {
        "tipo": "ingreso",
        "monto": 5000.00,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "descripcion": "   ",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "metodo_pago": "transferencia",
        "origen": "manual",
    }

    response = client.post("/transacciones", json=payload)
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["descripcion"] == ""
    assert float(data["monto"]) == 5000.00


def test_editar_transaccion_sin_descripcion(client, setup_data, db_session):
    """Prueba que PATCH /transacciones/{id} permita actualizar dejando descripción vacía."""
    billetera = setup_data["billetera"]
    categoria = setup_data["categoria"]

    # Crear transacción inicial con descripción
    res_crear = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 1000.00,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "descripcion": "Compra inicial",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_crear.status_code == status.HTTP_201_CREATED
    tx_id = res_crear.json()["id"]

    # Actualizar a descripción vacía
    res_patch = client.patch(f"/transacciones/{tx_id}", json={
        "descripcion": "",
    })
    assert res_patch.status_code == status.HTTP_200_OK
    assert res_patch.json()["descripcion"] == ""


def test_crear_transaccion_campos_obligatorios_siguen_fallando(client, setup_data):
    """
    Confirma que monto, billetera_id, categoria_id, metodo_pago, tipo, moneda y fecha
    SON obligatorios y devuelven 422 si se omiten o son inválidos.
    """
    billetera = setup_data["billetera"]
    categoria = setup_data["categoria"]

    # 1. Falta monto
    res_no_monto = client.post("/transacciones", json={
        "tipo": "egreso",
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_no_monto.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 2. Monto <= 0
    res_monto_cero = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 0,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_monto_cero.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 3. Falta billetera_id
    res_no_billetera = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 100,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "categoria_id": str(categoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_no_billetera.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 4. Falta fecha
    res_no_fecha = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 100,
        "moneda": "ARS",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_no_fecha.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 5. Falta categoria_id
    res_no_categoria = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 100,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "billetera_id": str(billetera.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_no_categoria.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    # 6. Categoria inexistente (404)
    res_cat_inexistente = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 100,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "billetera_id": str(billetera.id),
        "categoria_id": str(uuid4()),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_cat_inexistente.status_code == status.HTTP_404_NOT_FOUND


def test_editar_transaccion_preserva_descripcion_si_no_se_envia(client, setup_data):
    """Prueba que un PATCH que solo actualiza fecha o monto NO pise ni borre la descripción existente."""
    billetera = setup_data["billetera"]
    categoria = setup_data["categoria"]

    res_crear = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 1200.00,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "descripcion": "Mi Compra Valiosa",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_crear.status_code == status.HTTP_201_CREATED
    tx_id = res_crear.json()["id"]

    # PATCH cambiando solo monto y fecha (sin incluir descripcion en el body)
    res_patch = client.patch(f"/transacciones/{tx_id}", json={
        "monto": 1500.00,
        "fecha": "2026-08-28"
    })
    assert res_patch.status_code == status.HTTP_200_OK
    data = res_patch.json()
    assert float(data["monto"]) == 1500.00
    assert data["fecha"] == "2026-08-28"
    # La descripción DEBE seguir intacta
    assert data["descripcion"] == "Mi Compra Valiosa"


def test_editar_transaccion_descripcion_null_limpia_a_vacio(client, setup_data):
    """Prueba que si un cliente envía descripcion: null explícitamente en el PATCH, se limpie a string vacío sin romper."""
    billetera = setup_data["billetera"]
    categoria = setup_data["categoria"]

    res_crear = client.post("/transacciones", json={
        "tipo": "egreso",
        "monto": 800.00,
        "moneda": "ARS",
        "fecha": "2026-08-27",
        "descripcion": "Texto a borrar",
        "billetera_id": str(billetera.id),
        "categoria_id": str(categoria.id),
        "metodo_pago": "debito",
        "origen": "manual",
    })
    assert res_crear.status_code == status.HTTP_201_CREATED
    tx_id = res_crear.json()["id"]

    # PATCH enviando descripcion null explícitamente
    res_patch = client.patch(f"/transacciones/{tx_id}", json={
        "descripcion": None
    })
    assert res_patch.status_code == status.HTTP_200_OK
    assert res_patch.json()["descripcion"] == ""

