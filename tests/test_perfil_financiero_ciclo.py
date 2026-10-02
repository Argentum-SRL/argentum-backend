import pytest
from datetime import date, timedelta
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool

# Override JSONB type compilation for SQLite in tests
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, CicloTipo, CicloAjusteDireccion, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, OrigenTransaccion
from app.models.feriado import FeriadoAR
from app.services.perfil_financiero_service import (
    _calcular_y_persistir_perfil_sync,
)
from app.services.dias_habiles_service import _feriados_cache
from app.services.dashboard_service import get_ciclo_fechas

# In-memory SQLite database setup
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
    """Inicializa la DB de prueba y puebla feriados_ar y cache."""
    import app.models
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

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


# ==============================================================================
# 2. Tests para _calcular_y_persistir_perfil_sync con REGLA
# ==============================================================================

def test_frecuencia_financiera_con_ciclo_regla_no_rompe(db_session):
    """
    Usuario con ciclo_tipo=REGLA (ultimo_viernes).
    Verifica que get_ciclo_fechas resuelve correctamente y no intenta castear a int.
    """
    usuario = Usuario(
        id=uuid4(),
        email="test_frecuencia_regla@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.REGLA,
        ciclo_valor="ultimo_viernes",
        ciclo_ajuste_direccion=CicloAjusteDireccion.ANTERIOR,
    )
    db_session.add(usuario)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("300000"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(billetera)

    # Crear transacciones con más de 90 días de historial
    tx_antigua = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.INGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Ingreso Test",
        monto=Decimal("150000"),
        moneda=Moneda.ARS,
        fecha=date.today() - timedelta(days=100),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx_reciente = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Gasto Test",
        monto=Decimal("50000"),
        moneda=Moneda.ARS,
        fecha=date.today() - timedelta(days=5),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add_all([tx_antigua, tx_reciente])
    db_session.commit()

    # Ejecutar sin error
    perfil = _calcular_y_persistir_perfil_sync(db_session, usuario.id)
    assert perfil is not None


def test_usuario_sin_ciclo_fallback_seguro(db_session):
    """
    Usuario con ciclo_tipo=None funciona con mes calendario como fallback seguro.
    Verifica que _calcular_y_persistir_perfil_sync devuelve un perfil válido sin errores.
    """
    usuario = Usuario(
        id=uuid4(),
        email="test_sin_ciclo_fallback@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=None,
        ciclo_valor=None,
    )
    db_session.add(usuario)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("300000"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(billetera)

    tx_antigua = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.INGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Ingreso Test",
        monto=Decimal("150000"),
        moneda=Moneda.ARS,
        fecha=date.today() - timedelta(days=100),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx_reciente = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Gasto Test",
        monto=Decimal("50000"),
        moneda=Moneda.ARS,
        fecha=date.today() - timedelta(days=5),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add_all([tx_antigua, tx_reciente])
    db_session.commit()

    perfil = _calcular_y_persistir_perfil_sync(db_session, usuario.id)
    assert perfil is not None
