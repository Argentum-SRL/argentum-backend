"""
TE.9b: Pruebas unitarias de cálculo de cuotas unificado y tasas en TNA (tests/test_cuotas_tna.py).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import cuotas_service, transaccion_service
from app.utils.cuotas import calcular_plan_cuotas
from app.utils.tasas import tna_a_mensual


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

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

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    usuario = Usuario(
        id=uuid4(),
        email="test_cuotas_tna@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_test: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_test

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_te9b_tna_a_mensual():
    """TE.9b: Conversión exacta de TNA a tasa mensual."""
    assert tna_a_mensual(60) == Decimal("0.05")
    assert tna_a_mensual(12) == Decimal("0.01")
    assert tna_a_mensual(Decimal("54")) == Decimal("0.045")


def test_te9b_calcular_plan_cuotas():
    """TE.9b: Cálculo unificado de plan de cuotas (monto_cuota, total_financiado, ultima_cuota)."""
    # 120.000 en 12 cuotas con TNA 60%
    p1 = calcular_plan_cuotas(Decimal("120000"), 12, tna_a_mensual(60))
    assert p1["monto_cuota"] == Decimal("13539.05")
    assert p1["total_financiado"] == Decimal("162468.59")
    assert p1["ultima_cuota"] == Decimal("13539.04")

    # 100.000 en 3 cuotas sin interés
    p2 = calcular_plan_cuotas(Decimal("100000"), 3, None)
    assert p2["monto_cuota"] == Decimal("33333.33")
    assert p2["total_financiado"] == Decimal("100000.00")
    assert p2["ultima_cuota"] == Decimal("33333.34")

    # 100.000 en 6 cuotas sin interés
    p3 = calcular_plan_cuotas(Decimal("100000"), 6, None)
    assert p3["monto_cuota"] == Decimal("16666.67")
    assert p3["total_financiado"] == Decimal("100000.00")
    assert p3["ultima_cuota"] == Decimal("16666.65")

    # 120.000 en 1 cuota con TNA 60%
    p4 = calcular_plan_cuotas(Decimal("120000"), 1, tna_a_mensual(60))
    assert p4["monto_cuota"] == Decimal("126000.00")
    assert p4["total_financiado"] == Decimal("126000.00")
    assert p4["ultima_cuota"] == Decimal("126000.00")


def test_te9b_calcular_tasas_cuotas():
    """TE.9b: Cálculo de TNA, TEA y CFT estimado a partir de TNA."""
    t60 = cuotas_service.calcular_tasas_cuotas(Decimal("60"))
    assert t60 is not None
    assert t60["tna"] == Decimal("60.00")
    assert t60["tea"] == Decimal("79.59")
    assert t60["cft_estimado"] == Decimal("102.36")

    t54 = cuotas_service.calcular_tasas_cuotas(Decimal("54"))
    assert t54 is not None
    assert t54["tna"] == Decimal("54.00")
    assert t54["tea"] == Decimal("69.59")
    assert t54["cft_estimado"] == Decimal("88.93")

    assert cuotas_service.calcular_tasas_cuotas(None) is None
    assert cuotas_service.calcular_tasas_cuotas(Decimal("0")) is None


def test_te9b_endpoint_simular_cuotas_con_interes(client: TestClient):
    """TE.9b: GET /grupos-cuotas/simular con monto 120000, cantidades 12 y 3, tasa 60 y contado 100000."""
    res = client.get(
        "/grupos-cuotas/simular",
        params=[
            ("monto_total", "120000"),
            ("cantidades", "12"),
            ("cantidades", "3"),
            ("tasa_tna", "60"),
            ("precio_contado", "100000"),
        ]
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["tna"] == 60.0
    assert data["tea"] == 79.59
    assert data["cft_estimado"] == 102.36

    planes = data["planes"]
    assert len(planes) == 2

    # Plan 12 cuotas
    assert planes[0]["cantidad_cuotas"] == 12
    assert planes[0]["monto_cuota"] == 13539.05
    assert planes[0]["total_financiado"] == 162468.59
    assert planes[0]["ultima_cuota"] == 13539.04
    assert planes[0]["recargo"] == 62468.59
    assert planes[0]["recargo_porcentaje"] == 62.47

    # Plan 3 cuotas
    assert planes[1]["cantidad_cuotas"] == 3
    assert planes[1]["monto_cuota"] == 44065.03
    assert planes[1]["total_financiado"] == 132195.08
    assert planes[1]["ultima_cuota"] == 44065.02
    assert planes[1]["recargo"] == 32195.08
    assert planes[1]["recargo_porcentaje"] == 32.2


def test_te9b_endpoint_simular_cuotas_sin_interes(client: TestClient):
    """TE.9b: GET /grupos-cuotas/simular con monto 100000, cantidades 3 y 6 sin tasa ni precio_contado."""
    res = client.get(
        "/grupos-cuotas/simular",
        params=[
            ("monto_total", "100000"),
            ("cantidades", "3"),
            ("cantidades", "6"),
        ]
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["tna"] is None
    assert data["tea"] is None
    assert data["cft_estimado"] is None

    planes = data["planes"]
    assert len(planes) == 2

    assert planes[0]["cantidad_cuotas"] == 3
    assert planes[0]["monto_cuota"] == 33333.33
    assert planes[0]["total_financiado"] == 100000.0
    assert planes[0]["ultima_cuota"] == 33333.34
    assert planes[0]["recargo"] is None
    assert planes[0]["recargo_porcentaje"] is None

    assert planes[1]["cantidad_cuotas"] == 6
    assert planes[1]["monto_cuota"] == 16666.67
    assert planes[1]["total_financiado"] == 100000.0
    assert planes[1]["ultima_cuota"] == 16666.65
    assert planes[1]["recargo"] is None
    assert planes[1]["recargo_porcentaje"] is None


def test_te9b_endpoint_simular_validaciones_422(client: TestClient):
    """TE.9b: Validaciones fuera de rango devuelven 422."""
    # cantidades=0
    r0 = client.get("/grupos-cuotas/simular", params=[("monto_total", "100000"), ("cantidades", "0")])
    assert r0.status_code == 422

    # cantidades=121
    r121 = client.get("/grupos-cuotas/simular", params=[("monto_total", "100000"), ("cantidades", "121")])
    assert r121.status_code == 422

    # Siete cantidades (máximo 6)
    r7 = client.get(
        "/grupos-cuotas/simular",
        params=[
            ("monto_total", "100000"),
            ("cantidades", "1"),
            ("cantidades", "2"),
            ("cantidades", "3"),
            ("cantidades", "4"),
            ("cantidades", "5"),
            ("cantidades", "6"),
            ("cantidades", "7"),
        ]
    )
    assert r7.status_code == 422

    # tasa_tna=3001
    rt = client.get("/grupos-cuotas/simular", params=[("monto_total", "100000"), ("cantidades", "3"), ("tasa_tna", "3001")])
    assert rt.status_code == 422


def test_te9b_endpoint_simular_sin_usuario_401(db: Session):
    """TE.9b: Sin usuario autenticado devuelve 401."""
    def override_get_db():
        yield db

    prev = dict(app.dependency_overrides)
    try:
        app.dependency_overrides.clear()
        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as client_sin_auth:
            res = client_sin_auth.get("/grupos-cuotas/simular", params=[("monto_total", "100000"), ("cantidades", "3")])
            assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prev)


def test_te9b_crear_transaccion_y_get_grupos_cuotas(client: TestClient, db: Session, usuario_test: Usuario):
    """
    TE.9b: crear_transaccion de $120.000 con crédito en 12 cuotas con tasa_interes 60:
    GET /grupos-cuotas devuelve ese grupo con tasa_interes 60.0, tna 60.0, tea 79.59 y cft_estimado 102.36.
    """
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Santander",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
    )
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=b.id,
        nombre="Visa Santander",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=5,
        estado=EstadoTarjeta.ACTIVA,
        moneda=Moneda.ARS,
    )
    cat = Categoria(
        id=uuid4(),
        nombre="Tecnología",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([b, tarjeta, cat])
    db.commit()

    tx_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Smart TV",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.CREDITO,
        billetera_id=b.id,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        primer_vencimiento_manual=date(2026, 11, 5),
        info_cuotas=InfoCuotas(
            cantidad_cuotas=12,
            cuota_inicial=1,
            tiene_interes=True,
            tasa_interes=Decimal("60"),
            monto_total=Decimal("120000.00"),
            proximo_resumen=False,
        ),
    )

    tx_creada = transaccion_service.crear_transaccion(db, usuario_test.id, tx_data, commit=True)
    assert tx_creada.grupo_cuotas_id is not None

    res = client.get("/grupos-cuotas")
    assert res.status_code == 200, res.text
    grupos = res.json()
    assert len(grupos) >= 1

    grupo_tv = next((g for g in grupos if str(g["id"]) == str(tx_creada.grupo_cuotas_id)), None)
    assert grupo_tv is not None
    assert grupo_tv["tasa_interes"] == 60.0
    assert grupo_tv["tna"] == 60.0
    assert grupo_tv["tea"] == 79.59
    assert grupo_tv["cft_estimado"] == 102.36

