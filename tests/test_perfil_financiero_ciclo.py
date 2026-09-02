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
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, OrigenTransaccion
from app.models.feriado import FeriadoAR
from app.services.perfil_financiero_service import (
    _calcular_ratio_cuotas_sync_moneda,
    _calcular_y_persistir_perfil_sync,
)
from app.services.transaccion_service import evaluar_gasto_inusual
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
# 1. Tests para _calcular_ratio_cuotas_sync_moneda con REGLA y DIA_FIJO
# ==============================================================================

def test_ratio_cuotas_con_ciclo_regla(db_session):
    """
    Usuario con ciclo_tipo=REGLA (ultimo_viernes).
    Las cuotas a computar deben delimitarse por get_ciclo_fechas y no por mes calendario 1..31.
    """
    usuario = Usuario(
        id=uuid4(),
        email="test_cuotas_regla@argentum.com",
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
        nombre="Efectivo",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(billetera)

    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Compra financiada",
        monto=Decimal("30000"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        es_padre_cuotas=True,
    )
    db_session.add(tx_padre)

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Compra financiada",
        monto_total=Decimal("30000"),
        total_financiado=Decimal("30000"),
        cantidad_cuotas=3,
        moneda=Moneda.ARS,
    )
    db_session.add(grupo)

    inicio_ciclo, fin_ciclo = get_ciclo_fechas(usuario, date.today())

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Cuota 1",
        monto=Decimal("10000"),
        moneda=Moneda.ARS,
        fecha=inicio_ciclo + timedelta(days=2),
        es_cuota_hija=True,
    )
    db_session.add(tx_hija)

    # Cuota dentro del ciclo
    cuota_en_ciclo = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        monto_proyectado=Decimal("10000"),
        fecha_vencimiento=inicio_ciclo + timedelta(days=2),
        pagada=False,
    )
    db_session.add(cuota_en_ciclo)

    # Transacción de ingreso histórico para tener base de ingresos
    tx_ingreso = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.INGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Ingreso Test",
        monto=Decimal("200000"),
        moneda=Moneda.ARS,
        fecha=date.today() - timedelta(days=20),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx_ingreso)
    db_session.commit()

    # Ejecutar cálculo
    ratio = _calcular_ratio_cuotas_sync_moneda(db_session, usuario.id, date.today() - timedelta(days=60), Moneda.ARS)
    assert ratio is not None
    assert ratio > Decimal("0")


# ==============================================================================
# 2. Tests para _calcular_y_persistir_perfil_sync con REGLA
# ==============================================================================

def test_frecuencia_financiera_con_ciclo_regla_no_rompe(db_session):
    """
    Usuario con ciclo_tipo=REGLA (primer_lunes).
    Antes rompía con int("primer_lunes"). Ahora get_ciclo_fechas resuelve correctamente.
    """
    usuario = Usuario(
        id=uuid4(),
        email="test_frecuencia_regla@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.REGLA,
        ciclo_valor="primer_lunes",
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

    # Crear transacciones con más de 90 días de historial para pasar _validar_historial_minimo
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


# ==============================================================================
# 3. Tests para evaluar_gasto_inusual en transaccion_service con REGLA
# ==============================================================================

def test_evaluar_gasto_inusual_con_ciclo_regla(db_session):
    """
    evaluar_gasto_inusual (Nivel 2/3) no debe fallar cuando ciclo_tipo=REGLA (ultimo_viernes).
    """
    usuario = Usuario(
        id=uuid4(),
        email="test_inusual_regla@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.REGLA,
        ciclo_valor="ultimo_viernes",
    )
    db_session.add(usuario)

    cat = Categoria(
        id=uuid4(),
        nombre="Restaurantes",
        tipo=TipoCategoria.EGRESO,
    )
    db_session.add(cat)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("20000"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(billetera)

    # 35 transacciones de historial para activar nivel 2/3 (count >= 30)
    for i in range(35):
        tx_hist = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
            billetera_id=billetera.id,
            categoria_id=cat.id,
            tipo=TipoTransaccion.EGRESO,
            origen=OrigenTransaccion.MANUAL,
            descripcion="Gasto Test",
            monto=Decimal("5000"),
            moneda=Moneda.ARS,
            fecha=date.today() - timedelta(days=i + 1),
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        )
        db_session.add(tx_hist)

    # Transacción de ingreso previo para cálculo de ingreso promedio
    tx_ingreso = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tipo=TipoTransaccion.INGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Ingreso Test",
        monto=Decimal("500000"),
        moneda=Moneda.ARS,
        fecha=date.today() - timedelta(days=15),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx_ingreso)

    # Nueva transacción evaluada
    tx_eval = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Gasto Test",
        monto=Decimal("95000"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx_eval)
    db_session.commit()

    # Ejecutar evaluación de gasto inusual (ejecuta el bloque con get_ciclo_fechas sin errores)
    evaluar_gasto_inusual(db_session, usuario.id, tx_eval)


# ==============================================================================
# 4. Tests para usuarios sin ciclo configurado (Fallback seguro)
# ==============================================================================

def test_usuario_sin_ciclo_fallback_seguro(db_session):
    """
    Usuarios con ciclo_tipo=None funcionan normalmente con fallback a mes calendario.
    """
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

    cat = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
    )
    db_session.add(cat)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Efectivo",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(billetera)

    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        descripcion="Gasto Test",
        monto=Decimal("1000"),
        moneda=Moneda.ARS,
        fecha=date.today(),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx)
    db_session.commit()

    evaluar_gasto_inusual(db_session, usuario.id, tx)
