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
from app.models.categoria import Categoria, TipoCategoria
from app.models.presupuesto import Presupuesto, PeriodoPresupuestoTipo, RenovacionPresupuesto, EstadoPresupuesto
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.feriado import FeriadoAR
from app.schemas.presupuesto import PresupuestoCreate, PresupuestoCategoriaInput
from app.services.presupuesto_service import (
    calcular_fechas_periodo,
    crear_presupuesto,
    actualizar_presupuesto,
    reanudar_presupuesto,
    renovar_presupuestos,
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
# 1. Tests unitarios para calcular_fechas_periodo
# ==============================================================================

def test_calcular_fechas_periodo_mensual_con_usuario_dia_fijo(db_session):
    """Presupuesto MENSUAL con usuario DIA_FIJO debe coincidir exactamente con get_ciclo_fechas."""
    usuario = Usuario(
        id=uuid4(),
        email="test_mensual_dia_fijo@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="25",
        ciclo_ajuste_direccion=CicloAjusteDireccion.ANTERIOR,
    )
    db_session.add(usuario)
    db_session.commit()

    hoy = date(2026, 12, 26)
    inicio_esperado, fin_esperado = get_ciclo_fechas(usuario, hoy)

    inicio_calc, fin_calc = calcular_fechas_periodo(PeriodoPresupuestoTipo.MENSUAL, hoy, usuario=usuario)

    assert (inicio_calc, fin_calc) == (inicio_esperado, fin_esperado)
    assert inicio_calc == date(2026, 12, 24) # Ajustado por Navidad (25/12)

def test_calcular_fechas_periodo_mensual_con_usuario_regla(db_session):
    """Presupuesto MENSUAL con usuario REGLA debe coincidir exactamente con get_ciclo_fechas."""
    usuario = Usuario(
        id=uuid4(),
        email="test_mensual_regla@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.REGLA,
        ciclo_valor="ultimo_viernes",
        ciclo_ajuste_direccion=CicloAjusteDireccion.POSTERIOR,
    )
    db_session.add(usuario)
    db_session.commit()

    hoy = date(2026, 12, 28)
    inicio_esperado, fin_esperado = get_ciclo_fechas(usuario, hoy)

    inicio_calc, fin_calc = calcular_fechas_periodo(PeriodoPresupuestoTipo.MENSUAL, hoy, usuario=usuario)

    assert (inicio_calc, fin_calc) == (inicio_esperado, fin_esperado)
    assert inicio_calc == date(2026, 12, 28) # Ajustado hacia adelante saltando fin de semana

def test_calcular_fechas_periodo_mensual_sin_usuario_fallback_calendario():
    """Presupuesto MENSUAL sin usuario debe hacer fallback a mes calendario."""
    fecha_ref = date(2026, 8, 15)
    inicio, fin = calcular_fechas_periodo(PeriodoPresupuestoTipo.MENSUAL, fecha_ref, usuario=None)
    assert inicio == date(2026, 8, 1)
    assert fin == date(2026, 8, 31)

def test_calcular_fechas_periodo_mensual_usuario_sin_ciclo_fallback_calendario(db_session):
    """Presupuesto MENSUAL con usuario sin ciclo configurado debe devolver mes calendario estándar."""
    usuario = Usuario(
        id=uuid4(),
        email="test_sin_ciclo@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=None,
        ciclo_valor=None,
    )
    db_session.add(usuario)
    db_session.commit()

    fecha_ref = date(2026, 8, 15)
    inicio, fin = calcular_fechas_periodo(PeriodoPresupuestoTipo.MENSUAL, fecha_ref, usuario=usuario)
    assert inicio == date(2026, 8, 1)
    assert fin == date(2026, 8, 31)

def test_calcular_fechas_periodo_quincenal_y_semanal_invariables(db_session):
    """QUINCENAL y SEMANAL no se alteran y mantienen su lógica intacta."""
    usuario = Usuario(
        id=uuid4(),
        email="test_quincenal@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="25",
    )
    db_session.add(usuario)
    db_session.commit()

    # Quincenal 1a quincena
    i_q1, f_q1 = calcular_fechas_periodo(PeriodoPresupuestoTipo.QUINCENAL, date(2026, 8, 10), usuario=usuario)
    assert i_q1 == date(2026, 8, 1)
    assert f_q1 == date(2026, 8, 15)

    # Quincenal 2a quincena
    i_q2, f_q2 = calcular_fechas_periodo(PeriodoPresupuestoTipo.QUINCENAL, date(2026, 8, 20), usuario=usuario)
    assert i_q2 == date(2026, 8, 16)
    assert f_q2 == date(2026, 8, 31)

    # Semanal (5/8/2026 es miércoles -> lunes 3 a domingo 9)
    i_sem, f_sem = calcular_fechas_periodo(PeriodoPresupuestoTipo.SEMANAL, date(2026, 8, 5), usuario=usuario)
    assert i_sem == date(2026, 8, 3)
    assert f_sem == date(2026, 8, 9)


# ==============================================================================
# 2. Tests de integración: Crear y renovar presupuestos con ciclo custom
# ==============================================================================

def test_crear_presupuesto_mensual_con_ciclo_custom(db_session):
    """Al crear un presupuesto MENSUAL, su primer PeriodoPresupuesto debe respetar el ciclo custom del usuario."""
    usuario = Usuario(
        id=uuid4(),
        email="user_crear_presu@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="15",
        ciclo_ajuste_direccion=CicloAjusteDireccion.ANTERIOR,
    )
    db_session.add(usuario)

    cat = Categoria(
        id=uuid4(),
        nombre="Comida",
        tipo=TipoCategoria.EGRESO,
        es_global=True,
    )
    db_session.add(cat)
    db_session.commit()

    presu_create = PresupuestoCreate(
        nombre="Supermercado Mensual",
        monto=Decimal("150000"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        categorias=[PresupuestoCategoriaInput(categoria_id=cat.id)]
    )

    presu = crear_presupuesto(db_session, usuario.id, presu_create)

    assert len(presu.periodos) == 1
    periodo = presu.periodos[0]

    inicio_esperado, fin_esperado = get_ciclo_fechas(usuario, date.today())
    assert periodo.fecha_inicio == inicio_esperado
    assert periodo.fecha_fin == fin_esperado

def test_renovar_presupuestos_mensual_con_ciclo_custom(db_session):
    """renovar_presupuestos debe crear el nuevo PeriodoPresupuesto con fechas del ciclo custom del usuario."""
    usuario = Usuario(
        id=uuid4(),
        email="user_renovar_presu@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="10",
        ciclo_ajuste_direccion=CicloAjusteDireccion.ANTERIOR,
    )
    db_session.add(usuario)

    cat = Categoria(
        id=uuid4(),
        nombre="Transporte",
        tipo=TipoCategoria.EGRESO,
        es_global=True,
    )
    db_session.add(cat)
    db_session.commit()

    presu = Presupuesto(
        usuario_id=usuario.id,
        nombre="Transporte Mensual",
        monto=Decimal("50000"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO
    )
    db_session.add(presu)
    db_session.flush()

    # Periodo anterior ya vencido
    periodo_vencido = PeriodoPresupuesto(
        presupuesto_id=presu.id,
        fecha_inicio=date.today() - timedelta(days=60),
        fecha_fin=date.today() - timedelta(days=31),
        monto_limite=Decimal("50000"),
        monto_usado=Decimal("20000"),
        superado=False
    )
    db_session.add(periodo_vencido)
    db_session.commit()

    # Ejecutar renovación
    renovar_presupuestos(db_session)

    db_session.refresh(presu)
    assert len(presu.periodos) == 2

    nuevo_periodo = max(presu.periodos, key=lambda p: p.fecha_inicio)
    inicio_esperado, fin_esperado = get_ciclo_fechas(usuario, date.today())
    assert nuevo_periodo.fecha_inicio == inicio_esperado
    assert nuevo_periodo.fecha_fin == fin_esperado
