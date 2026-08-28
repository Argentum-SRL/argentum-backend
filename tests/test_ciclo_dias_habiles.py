import pytest
from datetime import date, timedelta
from uuid import uuid4
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
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, CicloTipo, CicloAjusteDireccion
from app.models.feriado import FeriadoAR
from app.services.dias_habiles_service import (
    es_dia_habil,
    ajustar_fecha_habil_sync,
    calcular_fecha_cobro_sync,
    _feriados_cache,
)
from app.services.dashboard_service import get_date_by_rule, get_ciclo_fechas

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

# Lista canónica de feriados 2026 para testing
FERIADOS_2026 = [
    (date(2026, 1, 1), "Año nuevo"),
    (date(2026, 2, 16), "Carnaval"),
    (date(2026, 2, 17), "Carnaval"),
    (date(2026, 3, 23), "Puente turístico"),
    (date(2026, 3, 24), "Memoria y Justicia"),
    (date(2026, 4, 2), "Malvinas"),
    (date(2026, 4, 3), "Viernes Santo"),
    (date(2026, 5, 1), "Día del Trabajador"),
    (date(2026, 5, 25), "Revolución de Mayo"),
    (date(2026, 6, 15), "Martín Güemes"),
    (date(2026, 6, 20), "Manuel Belgrano"),
    (date(2026, 7, 9), "Independencia"),
    (date(2026, 7, 10), "Puente turístico"),
    (date(2026, 8, 17), "San Martín"),
    (date(2026, 10, 12), "Diversidad Cultural"),
    (date(2026, 11, 23), "Soberanía Nacional"),
    (date(2026, 12, 7), "Puente turístico"),
    (date(2026, 12, 8), "Inmaculada Concepción"),
    (date(2026, 12, 25), "Navidad"),
]

@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    """Inicializa la DB de prueba y puebla feriados_ar y cache en memoria."""
    import app.models
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    # Cargar feriados 2026 en DB y cache
    fechas_2026 = []
    for f_fecha, f_nom in FERIADOS_2026:
        session.add(FeriadoAR(fecha=f_fecha, nombre=f_nom, anio=2026))
        fechas_2026.append(f_fecha)
    session.commit()

    _feriados_cache[2026] = sorted(fechas_2026)

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        _feriados_cache.clear()

@pytest.fixture(name="client", scope="function")
def client_fixture(db_session):
    """Configura el TestClient con overrides de dependencias."""
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

@pytest.fixture(name="admin_user")
def admin_user_fixture(db_session):
    """Crea y autentica al usuario testingadmin@argentum.com."""
    global current_test_user
    admin = Usuario(
        id=uuid4(),
        email="testingadmin@argentum.com",
        rol=RolUsuario.ADMIN,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        is_admin=True,
    )
    db_session.add(admin)
    db_session.commit()
    current_test_user = admin
    return admin


# ==============================================================================
# 1. Tests para es_dia_habil
# ==============================================================================

def test_es_dia_habil_lunes_habil():
    """Un lunes sin feriados debe ser día hábil."""
    lunes = date(2026, 8, 3) # Lunes
    feriados = [date(2026, 12, 25)]
    assert es_dia_habil(lunes, feriados) is True

def test_es_dia_habil_sabado():
    """Un sábado no debe ser día hábil."""
    sabado = date(2026, 8, 8) # Sábado
    assert es_dia_habil(sabado, []) is False

def test_es_dia_habil_domingo():
    """Un domingo no debe ser día hábil."""
    domingo = date(2026, 8, 9) # Domingo
    assert es_dia_habil(domingo, []) is False

def test_es_dia_habil_feriado():
    """Una fecha incluida en la lista de feriados no debe ser día hábil."""
    feriado = date(2026, 12, 25) # Navidad (Viernes)
    assert es_dia_habil(feriado, [feriado]) is False


# ==============================================================================
# 2. Tests para ajustar_fecha_habil_sync
# ==============================================================================

def test_ajustar_fecha_habil_ya_es_habil(db_session):
    """Una fecha que ya es hábil debe devolverse sin cambios."""
    miercoles = date(2026, 8, 5) # Miércoles hábil
    assert ajustar_fecha_habil_sync(miercoles, "anterior") == miercoles
    assert ajustar_fecha_habil_sync(miercoles, "posterior") == miercoles

def test_ajustar_fecha_habil_sabado_anterior(db_session):
    """Un sábado con dirección anterior debe devolver el viernes previo."""
    sabado = date(2026, 8, 8)
    viernes = date(2026, 8, 7)
    assert ajustar_fecha_habil_sync(sabado, "anterior") == viernes

def test_ajustar_fecha_habil_sabado_posterior(db_session):
    """Un sábado con dirección posterior debe devolver el lunes siguiente."""
    sabado = date(2026, 8, 8)
    lunes = date(2026, 8, 10)
    assert ajustar_fecha_habil_sync(sabado, "posterior") == lunes

def test_ajustar_fecha_habil_navidad_2026_anterior(db_session):
    """Navidad 2026 (viernes 25) con dirección anterior debe dar el jueves 24."""
    navidad = date(2026, 12, 25)
    jueves = date(2026, 12, 24)
    assert ajustar_fecha_habil_sync(navidad, "anterior") == jueves

def test_ajustar_fecha_habil_navidad_2026_posterior(db_session):
    """Navidad 2026 (viernes 25) con dirección posterior debe saltar el fin de semana y dar lunes 28."""
    navidad = date(2026, 12, 25)
    lunes = date(2026, 12, 28)
    assert ajustar_fecha_habil_sync(navidad, "posterior") == lunes

def test_ajustar_fecha_habil_feriados_consecutivos_carnaval(db_session):
    """
    Carnaval 2026: lunes 16 y martes 17 son feriados.
    - Martes 17 hacia atrás salta lunes 16, domingo 15, sábado 14 -> Viernes 13.
    - Lunes 16 hacia adelante salta martes 17 -> Miércoles 18.
    """
    martes_carnaval = date(2026, 2, 17)
    viernes_previo = date(2026, 2, 13)
    assert ajustar_fecha_habil_sync(martes_carnaval, "anterior") == viernes_previo

    lunes_carnaval = date(2026, 2, 16)
    miercoles_posterior = date(2026, 2, 18)
    assert ajustar_fecha_habil_sync(lunes_carnaval, "posterior") == miercoles_posterior

def test_ajustar_fecha_habil_semana_santa_malvinas(db_session):
    """
    Abril 2026: Jueves 2 (Malvinas) y Viernes 3 (Viernes Santo).
    - Jueves 2 hacia adelante salta viernes 3, sábado 4, domingo 5 -> Lunes 6.
    - Viernes 3 hacia atrás salta jueves 2 -> Miércoles 1.
    """
    jueves_malvinas = date(2026, 4, 2)
    lunes_posterior = date(2026, 4, 6)
    assert ajustar_fecha_habil_sync(jueves_malvinas, "posterior") == lunes_posterior

    viernes_santo = date(2026, 4, 3)
    miercoles_anterior = date(2026, 4, 1)
    assert ajustar_fecha_habil_sync(viernes_santo, "anterior") == miercoles_anterior


# ==============================================================================
# 3. Tests para get_date_by_rule (dashboard_service.py)
# ==============================================================================

def test_get_date_by_rule_primer_lunes():
    """Primer lunes de Agosto 2026: el 1/8 es sábado, por lo que es el 3/8."""
    assert get_date_by_rule("primer_lunes", 8, 2026) == date(2026, 8, 3)

def test_get_date_by_rule_ultimo_viernes_diciembre_2026():
    """Último viernes de Diciembre 2026: 31/12 es jueves, el último viernes es 25/12."""
    assert get_date_by_rule("ultimo_viernes", 12, 2026) == date(2026, 12, 25)

def test_get_date_by_rule_primer_miercoles():
    """Primer miércoles de Enero 2026: 1/1 es jueves, primer miércoles es 7/1."""
    assert get_date_by_rule("primer_miercoles", 1, 2026) == date(2026, 1, 7)

def test_get_date_by_rule_ultimo_jueves():
    """Último jueves de Diciembre 2026: 31/12 es jueves, devuelve 31/12."""
    assert get_date_by_rule("ultimo_jueves", 12, 2026) == date(2026, 12, 31)

def test_get_date_by_rule_invalida_fallback():
    """Una regla inválida debe devolver el primer día del mes como fallback seguro."""
    assert get_date_by_rule("regla_inexistente", 8, 2026) == date(2026, 8, 1)


# ==============================================================================
# 4. Tests para get_ciclo_fechas (integración día fijo y regla)
# ==============================================================================

def test_get_ciclo_fechas_dia_fijo_anterior(db_session):
    """
    Usuario DIA_FIJO día 25 con dirección anterior en Diciembre 2026 (Navidad 25/12).
    Inicio de ciclo debe ajustarse al jueves 24/12/2026.
    """
    usuario = Usuario(
        id=uuid4(),
        email="user_test1@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="25",
        ciclo_ajuste_direccion=CicloAjusteDireccion.ANTERIOR,
    )
    hoy = date(2026, 12, 26)
    inicio, fin = get_ciclo_fechas(usuario, hoy)
    assert inicio == date(2026, 12, 24)

def test_get_ciclo_fechas_dia_fijo_posterior(db_session):
    """
    Usuario DIA_FIJO día 25 con dirección posterior en Diciembre 2026.
    Inicio de ciclo debe ajustarse al lunes 28/12/2026.
    """
    usuario = Usuario(
        id=uuid4(),
        email="user_test2@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="25",
        ciclo_ajuste_direccion=CicloAjusteDireccion.POSTERIOR,
    )
    hoy = date(2026, 12, 28)
    inicio, fin = get_ciclo_fechas(usuario, hoy)
    assert inicio == date(2026, 12, 28)

def test_get_ciclo_fechas_regla_direccion_none_default_anterior(db_session):
    """
    Usuario REGLA ultimo_viernes con ciclo_ajuste_direccion=None.
    Debe asumir default 'anterior' sin fallar y dar 24/12/2026.
    """
    usuario = Usuario(
        id=uuid4(),
        email="user_test3@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=CicloTipo.REGLA,
        ciclo_valor="ultimo_viernes",
        ciclo_ajuste_direccion=None,
    )
    hoy = date(2026, 12, 26)
    inicio, fin = get_ciclo_fechas(usuario, hoy)
    assert inicio == date(2026, 12, 24)

def test_get_ciclo_fechas_regla_direccion_posterior(db_session):
    """
    Usuario REGLA ultimo_viernes con dirección posterior.
    Inicio debe dar lunes 28/12/2026.
    """
    usuario = Usuario(
        id=uuid4(),
        email="user_test4@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        ciclo_tipo=CicloTipo.REGLA,
        ciclo_valor="ultimo_viernes",
        ciclo_ajuste_direccion=CicloAjusteDireccion.POSTERIOR,
    )
    hoy = date(2026, 12, 28)
    inicio, fin = get_ciclo_fechas(usuario, hoy)
    assert inicio == date(2026, 12, 28)


# ==============================================================================
# 5. Tests para Endpoint preview-fecha-cobro (TestClient)
# ==============================================================================

def test_preview_fecha_cobro_dia_fijo(client, admin_user):
    """Prueba GET /onboarding/preview-fecha-cobro con dia_fijo."""
    response = client.get("/onboarding/preview-fecha-cobro?tipo=dia_fijo&valor=25&direccion=anterior")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["tipo"] == "dia_fijo"
    assert data["valor"] == "25"
    assert data["direccion"] == "anterior"
    assert "proxima_fecha_cobro" in data
    assert isinstance(data["fue_ajustada"], bool)

def test_preview_fecha_cobro_regla(client, admin_user):
    """Prueba GET /onboarding/preview-fecha-cobro con regla."""
    response = client.get("/onboarding/preview-fecha-cobro?tipo=regla&valor=ultimo_viernes&direccion=posterior")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["tipo"] == "regla"
    assert data["valor"] == "ultimo_viernes"
    assert data["direccion"] == "posterior"
    assert "proxima_fecha_cobro" in data
    assert isinstance(data["fue_ajustada"], bool)

def test_preview_fecha_cobro_tipo_invalido(client, admin_user):
    """Prueba que un tipo inválido retorne error 422 de validación de enum."""
    response = client.get("/onboarding/preview-fecha-cobro?tipo=invalido&valor=1")
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

def test_preview_fecha_cobro_dia_fijo_valor_fuera_de_rango(client, admin_user):
    """Prueba que un día fuera del rango 1-31 devuelva error 400."""
    response = client.get("/onboarding/preview-fecha-cobro?tipo=dia_fijo&valor=99")
    assert response.status_code == status.HTTP_400_BAD_REQUEST

def test_preview_fecha_cobro_regla_valor_invalido(client, admin_user):
    """Prueba que una regla no existente devuelva error 400."""
    response = client.get("/onboarding/preview-fecha-cobro?tipo=regla&valor=regla_inexistente")
    assert response.status_code == status.HTTP_400_BAD_REQUEST

def test_dashboard_periodo_actual(client, admin_user):
    """Prueba GET /dashboard/periodo-actual."""
    response = client.get("/dashboard/periodo-actual")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "fecha_inicio" in data
    assert "fecha_fin" in data

def test_actualizar_ciclo_dia_fijo_invalido_devuelve_422(client, admin_user):
    """Prueba que un día > 31 o no numérico en PUT /usuarios/me/ciclo-financiero retorne 422."""
    resp_35 = client.put("/usuarios/me/ciclo-financiero", json={
        "ciclo_tipo": "dia_fijo",
        "ciclo_valor": "35",
        "ciclo_ajuste_direccion": "anterior"
    })
    assert resp_35.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    resp_abc = client.put("/usuarios/me/ciclo-financiero", json={
        "ciclo_tipo": "dia_fijo",
        "ciclo_valor": "abc",
        "ciclo_ajuste_direccion": "anterior"
    })
    assert resp_abc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

def test_actualizar_ciclo_regla_invalida_devuelve_422(client, admin_user):
    """Prueba que una regla no existente en PUT /usuarios/me/ciclo-financiero retorne 422."""
    resp = client.put("/usuarios/me/ciclo-financiero", json={
        "ciclo_tipo": "regla",
        "ciclo_valor": "segundo_martes",
        "ciclo_ajuste_direccion": "anterior"
    })
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

def test_actualizar_ciclo_direccion_invalida_devuelve_422(client, admin_user):
    """Prueba que una dirección no existente retorne 422."""
    resp = client.put("/usuarios/me/ciclo-financiero", json={
        "ciclo_tipo": "dia_fijo",
        "ciclo_valor": "15",
        "ciclo_ajuste_direccion": "hacia_arriba"
    })
    assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_job_refresh_feriados_falla_api_sin_crashear(db_session, monkeypatch):
    """
    Prueba que si la API externa de feriados falla (timeout / 500), el job de refresh
    atrapa el error limpiamente sin lanzar excepción que rompa el scheduler,
    dejando la BD en estado consistente sin datos parciales.
    """
    from unittest.mock import patch, AsyncMock
    import httpx
    from app.main import _job_refresh_feriados

    _feriados_cache.clear()
    db_session.query(FeriadoAR).delete()
    db_session.commit()

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: TestingSessionLocal())

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get.side_effect = httpx.ConnectTimeout("Timeout conectando a API externa")

    with patch("httpx.AsyncClient", return_value=mock_client):
        # Debe completar limpiamente sin lanzar excepciones no controladas
        await _job_refresh_feriados()

    # Confirmar que la base de datos no quedó con datos corruptos o parciales
    total_feriados = db_session.query(FeriadoAR).count()
    assert total_feriados == 0


@pytest.mark.anyio
async def test_job_refresh_feriados_recuperacion_al_dia_siguiente(db_session, monkeypatch):
    """
    Prueba que tras una falla, al correr nuevamente el job con la API disponible (simulando
    la ejecución del día siguiente), se auto-recupera poblando los feriados en BD y en caché.
    """
    from unittest.mock import patch, MagicMock, AsyncMock
    from app.main import _job_refresh_feriados

    _feriados_cache.clear()
    db_session.query(FeriadoAR).delete()
    db_session.commit()

    monkeypatch.setattr("app.core.database.SessionLocal", lambda: TestingSessionLocal())

    hoy_anio = date.today().year
    fake_api_data = [
        {"fecha": f"{hoy_anio}-01-01", "nombre": "Año Nuevo", "tipo": "inamovible"},
        {"fecha": f"{hoy_anio}-05-01", "nombre": "Día del Trabajador", "tipo": "inamovible"},
        {"fecha": f"{hoy_anio + 1}-01-01", "nombre": "Año Nuevo Siguiente", "tipo": "inamovible"}
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = fake_api_data

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value.get.return_value = mock_response

    with patch("httpx.AsyncClient", return_value=mock_client):
        await _job_refresh_feriados()

    # Confirmar que los feriados se persistieron en BD
    feriados_guardados = db_session.query(FeriadoAR).all()
    assert len(feriados_guardados) >= 2
    fechas_guardadas = {f.fecha for f in feriados_guardados}
    assert date(hoy_anio, 1, 1) in fechas_guardadas
    assert date(hoy_anio, 5, 1) in fechas_guardadas

    # Confirmar que la caché en memoria también fue poblada
    assert hoy_anio in _feriados_cache
    assert date(hoy_anio, 1, 1) in _feriados_cache[hoy_anio]


def test_timezone_argentina_en_ventana_nocturna(client, admin_user):
    """
    Prueba que a las 22:30 hora Argentina (01:30 UTC del día siguiente),
    hoy_argentina() y /dashboard/periodo-actual operen con la fecha de Argentina y no con la de UTC.
    """
    from datetime import datetime, timezone
    from unittest.mock import patch
    from app.utils.fecha import hoy_argentina

    # Simular momento exacto: 2026-08-11 01:30:00 UTC -> 2026-08-10 22:30:00 en UTC-3
    utc_moment = datetime(2026, 8, 11, 1, 30, 0, tzinfo=timezone.utc)

    with patch("app.utils.fecha.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: utc_moment.astimezone(tz or timezone.utc)

        # En Argentina debe ser 10 de agosto (no 11)
        fecha_ar = hoy_argentina()
        assert fecha_ar == date(2026, 8, 10)

        # /dashboard/periodo-actual debe operar con fecha_ar
        resp = client.get("/dashboard/periodo-actual")
        assert resp.status_code == status.HTTP_200_OK




