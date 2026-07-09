import pytest
import io
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4, UUID
from unittest.mock import patch, MagicMock

from fastapi import status, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

from app.main import app, TimeoutMiddleware
from app.core.database import Base, get_db
from app.core.auth import get_current_admin_user
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.tarjeta_credito import TarjetaCredito, RedTarjeta, EstadoTarjeta
from app.models.importacion import ImportacionResumen, EstadoImportacion, CorreccionImportacion
from app.models.transaccion import Transaccion
from app.services.importacion.schemas import ResultadoParseo, TransaccionCruda


# Override JSONB type compilation for SQLite so that the test schema constructs successfully
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


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


def override_get_current_admin_user():
    if current_test_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado"
        )
    if not current_test_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés permiso para hacer eso."
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
    app.dependency_overrides[get_current_admin_user] = override_get_current_admin_user
    
    client = TestClient(app)
    yield client
    
    app.dependency_overrides.clear()


@pytest.fixture(name="seed_data", scope="function")
def seed_data_fixture(db_session):
    """Crea los datos semilla para las pruebas."""
    # 1. Admin user
    admin = Usuario(
        id=uuid4(),
        nombre="Admin",
        apellido="User",
        email="admin@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.ADMIN,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=True
    )
    # 2. Normal user
    normal = Usuario(
        id=uuid4(),
        nombre="Juan",
        apellido="Normal",
        email="juan@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        is_admin=False
    )
    # 3. Billeteras para el admin
    billetera_ars = Billetera(
        id=uuid4(),
        usuario_id=admin.id,
        nombre="Efectivo Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000.0"),
        saldo_inicial=Decimal("50000.0"),
        estado=EstadoBilletera.ACTIVA
    )
    billetera_usd = Billetera(
        id=uuid4(),
        usuario_id=admin.id,
        nombre="Efectivo Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("1000.0"),
        saldo_inicial=Decimal("1000.0"),
        estado=EstadoBilletera.ACTIVA
    )
    # 4. Tarjeta del admin
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=admin.id,
        billetera_id=billetera_ars.id,
        nombre="Visa Admin",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=30,
        limite_credito=Decimal("100000.0"),
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA
    )
    
    db_session.add_all([admin, normal, billetera_ars, billetera_usd, tarjeta])
    db_session.commit()
    
    return {
        "admin": admin,
        "normal": normal,
        "billetera_ars_id": billetera_ars.id,
        "billetera_usd_id": billetera_usd.id,
        "tarjeta_id": tarjeta.id
    }


def test_subir_no_pdf(client, seed_data):
    """Sube un archivo que no es PDF y verifica el error 400."""
    global current_test_user
    current_test_user = seed_data["admin"]
    
    file_data = {"archivo": ("test.txt", b"dummy content", "text/plain")}
    response = client.post("/importacion/procesar", files=file_data)
    
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    res_json = response.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "INVALID_FILE_TYPE"


def test_subir_pdf_demasiado_grande(client, seed_data):
    """Sube un archivo de más de 50MB y verifica el error 400."""
    global current_test_user
    current_test_user = seed_data["admin"]
    
    # Creamos un stream de bytes grande sin escribir en disco
    large_bytes = b"0" * (50 * 1024 * 1024 + 1)
    file_data = {"archivo": ("large.pdf", large_bytes, "application/pdf")}
    
    response = client.post("/importacion/procesar", files=file_data)
    
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    res_json = response.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "FILE_TOO_LARGE"


def test_acceder_sin_ser_admin(client, seed_data):
    """Intenta procesar sin credenciales de administrador y verifica el error 403."""
    global current_test_user
    current_test_user = seed_data["normal"]  # usuario no admin
    
    file_data = {"archivo": ("test.pdf", b"%PDF-1.4 dummy", "application/pdf")}
    response = client.post("/importacion/procesar", files=file_data)
    
    assert response.status_code == status.HTTP_403_FORBIDDEN
    res_json = response.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "FORBIDDEN"


def test_subir_pdf_valido_galicia(client, seed_data, db_session):
    """Sube un PDF válido y verifica la correcta creación de ImportacionResumen."""
    global current_test_user
    current_test_user = seed_data["admin"]
    
    mock_parse = ResultadoParseo(
        banco="galicia",
        titular_detectado="JUAN PEREZ",
        ultimos_4_digitos="1234",
        periodo_desde=date(2026, 6, 1),
        periodo_hasta=date(2026, 6, 30),
        transacciones=[
            TransaccionCruda(
                fecha=date(2026, 6, 15),
                descripcion="COMPRA SUPERMERCADO COTO",
                monto=Decimal("1500.50"),
                moneda="ARS",
                es_cargo_bancario=False,
                titular_seccion="JUAN PEREZ"
            )
        ],
        confianza=0.9,
        capa_usada="deterministic",
        escalado=False
    )
    
    with patch("app.routers.importacion.procesar_resumen", return_value=mock_parse):
        file_data = {"archivo": ("galicia.pdf", b"%PDF-1.4 mock", "application/pdf")}
        response = client.post("/importacion/procesar", files=file_data)
        
        assert response.status_code == status.HTTP_200_OK
        res_json = response.json()
        assert res_json["success"] is True
        data = res_json["data"]
        
        assert "importacion_id" in data
        assert data["banco_detectado"] == "galicia"
        assert data["estado"] == "pendiente_revision"
        assert data["total_detectadas"] == 1
        assert data["confianza"] == 0.9
        assert data["escalado"] is False
        
        # Verificar que el registro en DB tenga dueño correcto
        import_record = db_session.query(ImportacionResumen).filter_by(id=UUID(data["importacion_id"])).first()
        assert import_record is not None
        assert import_record.usuario_id == seed_data["admin"].id
        assert import_record.admin_id == seed_data["admin"].id


def test_preview_inexistente(client, seed_data):
    """Solicita previsualización de un ID inexistente y verifica el error 404."""
    global current_test_user
    current_test_user = seed_data["admin"]
    
    random_id = uuid4()
    response = client.get(f"/importacion/{random_id}/preview")
    
    assert response.status_code == status.HTTP_404_NOT_FOUND
    res_json = response.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "IMPORTACION_NOT_FOUND"


def test_preview_otro_usuario(client, seed_data, db_session):
    """Solicita previsualización de una importación de otro usuario y recibe 404."""
    # Creamos un resumen asignado al usuario normal
    otra_importacion = ImportacionResumen(
        id=uuid4(),
        usuario_id=seed_data["normal"].id,
        admin_id=seed_data["admin"].id,
        banco_detectado="galicia",
        nombre_archivo="otro.pdf",
        estado=EstadoImportacion.PENDIENTE_REVISION,
        total_detectadas=0
    )
    db_session.add(otra_importacion)
    db_session.commit()
    
    # Autenticamos como Admin (quien no es dueño de esa importación, el dueño es 'normal')
    global current_test_user
    current_test_user = seed_data["admin"]
    
    response = client.get(f"/importacion/{otra_importacion.id}/preview")
    
    # Debe ser 404 para ocultar su existencia ante el otro usuario
    assert response.status_code == status.HTTP_404_NOT_FOUND
    res_json = response.json()
    assert res_json["success"] is False
    assert res_json["error"]["code"] == "IMPORTACION_NOT_FOUND"


def test_flujo_completo_confirmar(client, seed_data, db_session):
    """Test completo del flujo de confirmación, incluyendo la previsualización y exclusiones."""
    global current_test_user
    current_test_user = seed_data["admin"]
    
    # 1. Crear registro resumen para previsualizar y confirmar
    importacion = ImportacionResumen(
        id=uuid4(),
        usuario_id=seed_data["admin"].id,
        admin_id=seed_data["admin"].id,
        banco_detectado="galicia",
        nombre_archivo="resumen.pdf",
        estado=EstadoImportacion.PENDIENTE_REVISION,
        transacciones_parseadas=[
            {
                "fecha": "2026-06-10",
                "descripcion": "COMPRA DIA 3/12",
                "monto": 150.0,
                "moneda": "ARS",
                "cuota_actual": 3,
                "cuota_total": 12,
                "es_cargo_bancario": False,
                "titular_seccion": "JUAN PEREZ"
            },
            {
                "fecha": "2026-06-11",
                "descripcion": "COMPRA EXCLUIDA",
                "monto": 500.0,
                "moneda": "ARS",
                "cuota_actual": None,
                "cuota_total": None,
                "es_cargo_bancario": False,
                "titular_seccion": "JUAN PEREZ"
            }
        ],
        total_detectadas=2
    )
    db_session.add(importacion)
    db_session.commit()
    
    # 2. Obtener Preview
    response_prev = client.get(f"/importacion/{importacion.id}/preview")
    assert response_prev.status_code == status.HTTP_200_OK
    data_prev = response_prev.json()["data"]
    assert len(data_prev["transacciones"]) == 2
    assert data_prev["transacciones"][0]["posible_duplicado"] is False
    
    # 3. Confirmar importación (excluyendo la segunda transacción)
    payload = {
        "tarjeta_id": str(seed_data["tarjeta_id"]),
        "billetera_id": str(seed_data["billetera_ars_id"]),
        "billetera_usd_id": str(seed_data["billetera_usd_id"]),
        "titulares_seleccionados": ["JUAN PEREZ"],
        "transacciones_finales": [
            {
                "categoria_id": str(uuid4()), # Categoría personalizada
                "incluir": True
            },
            {
                "categoria_id": None,
                "incluir": False  # Excluida
            }
        ]
    }
    
    response_conf = client.post(f"/importacion/{importacion.id}/confirmar", json=payload)
    assert response_conf.status_code == status.HTTP_200_OK
    
    data_conf = response_conf.json()["data"]
    assert data_conf["importadas"] == 1
    assert data_conf["total_procesadas"] == 1
    
    # Verificar estado en DB
    db_session.refresh(importacion)
    assert importacion.estado == EstadoImportacion.IMPORTADO
    assert importacion.total_excluidas == 1
    assert importacion.total_importadas == 1
    
    # 4. Intentar confirmar dos veces
    response_double = client.post(f"/importacion/{importacion.id}/confirmar", json=payload)
    assert response_double.status_code == status.HTTP_409_CONFLICT
    assert response_double.json()["error"]["code"] == "IMPORTACION_YA_CONFIRMADA"


def test_verificar_ruta_correcta(client, seed_data):
    """Verifica que las rutas respondan bajo /importacion y no /admin/importacion."""
    global current_test_user
    current_test_user = seed_data["admin"]
    
    # Una llamada inexistente a /admin/importacion debe dar 404
    resp_admin = client.get("/admin/importacion")
    assert resp_admin.status_code == status.HTTP_404_NOT_FOUND
    
    resp_correct = client.get(f"/importacion/{uuid4()}/preview")
    # Da 404 porque el id no existe, pero es el endpoint correcto de importacion, no de la app entera
    assert resp_correct.status_code == status.HTTP_404_NOT_FOUND
    assert resp_correct.json()["error"]["code"] == "IMPORTACION_NOT_FOUND"


@pytest.mark.anyio
async def test_timeout_middleware_adjustments():
    """Verifica el timeout correcto (100s para importación, 30s para el resto)."""
    async def dummy_app(scope, receive, send):
        pass
        
    middleware = TimeoutMiddleware(dummy_app, timeout=30.0)
    
    # 1. Ruta normal
    scope_normal = {"type": "http", "path": "/usuarios/me"}
    with patch("anyio.fail_after") as mock_fail:
        await middleware(scope_normal, MagicMock(), MagicMock())
        mock_fail.assert_called_once_with(30.0)
        
    # 2. Ruta de importación
    scope_import = {"type": "http", "path": "/importacion/procesar"}
    with patch("anyio.fail_after") as mock_fail_imp:
        await middleware(scope_import, MagicMock(), MagicMock())
        mock_fail_imp.assert_called_once_with(100.0)
