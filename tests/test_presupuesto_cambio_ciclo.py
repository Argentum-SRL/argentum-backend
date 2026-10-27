"""
Tests para reajuste de períodos de presupuestos ante cambio de ciclo financiero.
Casos 1 a 6 requeridos por el paso fase_e1_ciclo_c.
"""
import pytest
from datetime import date, timedelta
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool

# Compilación de JSONB para SQLite en entorno de testing
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, CicloTipo, CicloAjusteDireccion, Moneda
from app.models.categoria import Categoria, TipoCategoria
from app.models.presupuesto import Presupuesto, PeriodoPresupuestoTipo, RenovacionPresupuesto, EstadoPresupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
from app.models.feriado import FeriadoAR
from app.schemas.usuario import EditarCicloFinanciero
from app.services.dias_habiles_service import _feriados_cache
from app.services.usuario_service import actualizar_ciclo_financiero
from app.services.presupuesto_service import (
    reajustar_periodos_por_cambio_de_ciclo,
    renovar_presupuestos,
    obtener_periodo_activo,
    uso_periodo,
)

# Base de datos SQLite en memoria
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
    """Inicializa la DB SQLite en memoria y puebla feriados."""
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


def _crear_usuario(db, ciclo_valor="1"):
    usuario = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:8]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor=ciclo_valor,
        ciclo_ajuste_direccion=None,
        moneda_principal=Moneda.ARS,
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


def _crear_categoria(db):
    cat = Categoria(
        id=uuid4(),
        nombre="Supermercado",
        tipo=TipoCategoria.EGRESO,
        icono="shopping-cart",
        color="#4CAF50"
    )
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


def _crear_transaccion_gasto(db, usuario_id, categoria_id, fecha, monto=Decimal("5000.00")):
    from app.models.billetera import Billetera, EstadoBilletera
    from app.models.transaccion import OrigenTransaccion
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre="Efectivo",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(billetera)
    db.commit()

    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera.id,
        monto=monto,
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        origen=OrigenTransaccion.MANUAL,
        fecha=fecha,
        descripcion="Gasto de prueba",
        categoria_id=categoria_id,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


# ==============================================================================
# Casos 1 a 6
# ==============================================================================

def test_caso_1_cambio_dia_1_a_dia_28(db_session, monkeypatch):
    """
    Caso 1: Usuario con ciclo día 1; período anterior 2026-09-01 a 2026-09-30 y activo 2026-10-01 a 2026-10-31.
    Cambia a día 28 -> el activo queda 2026-10-01 a 2026-10-27, el anterior no cambia y monto_usado 5000.00.
    """
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 9))

    usuario = _crear_usuario(db_session, ciclo_valor="1")
    cat = _crear_categoria(db_session)
    _crear_transaccion_gasto(db_session, usuario.id, cat.id, fecha=date(2026, 10, 2), monto=Decimal("5000.00"))

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Comida mensual",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    presu.categorias.append(PresupuestoCategoria(categoria_id=cat.id))
    db_session.add(presu)
    db_session.commit()

    periodo_anterior = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 9, 1),
        fecha_fin=date(2026, 9, 30),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    periodo_activo = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2026, 10, 31),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("5000.00"),
        superado=False,
    )
    db_session.add_all([periodo_anterior, periodo_activo])
    db_session.commit()

    # Usuario cambia a día 28
    datos = EditarCicloFinanciero(ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="28")
    actualizar_ciclo_financiero(db_session, usuario, datos)

    db_session.refresh(periodo_activo)
    db_session.refresh(periodo_anterior)

    # Validaciones caso 1
    assert periodo_activo.fecha_inicio == date(2026, 10, 1)
    assert periodo_activo.fecha_fin == date(2026, 10, 27)
    uso_1 = uso_periodo(db_session, presu, periodo_activo, hoy=date(2026, 10, 9), usuario=usuario)
    assert uso_1.mostrado == Decimal("5000.00")
    assert uso_1.supera_limite is False

    # Período anterior no cambia
    assert periodo_anterior.fecha_inicio == date(2026, 9, 1)
    assert periodo_anterior.fecha_fin == date(2026, 9, 30)


def test_caso_2_cambio_dia_28_a_dia_1(db_session, monkeypatch):
    """
    Caso 2: Usuario con ciclo día 28; período anterior 2026-08-28 a 2026-09-27 y activo 2026-09-28 a 2026-10-27.
    Cambia a día 1 -> el activo queda 2026-09-28 a 2026-10-31, el anterior no cambia y monto_usado 5000.00.
    """
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 9))

    usuario = _crear_usuario(db_session, ciclo_valor="28")
    cat = _crear_categoria(db_session)
    _crear_transaccion_gasto(db_session, usuario.id, cat.id, fecha=date(2026, 10, 2), monto=Decimal("5000.00"))

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Comida mensual",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    presu.categorias.append(PresupuestoCategoria(categoria_id=cat.id))
    db_session.add(presu)
    db_session.commit()

    periodo_anterior = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 8, 28),
        fecha_fin=date(2026, 9, 27),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    periodo_activo = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 9, 28),
        fecha_fin=date(2026, 10, 27),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("5000.00"),
        superado=False,
    )
    db_session.add_all([periodo_anterior, periodo_activo])
    db_session.commit()

    # Usuario cambia a día 1
    datos = EditarCicloFinanciero(ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1")
    actualizar_ciclo_financiero(db_session, usuario, datos)

    db_session.refresh(periodo_activo)
    db_session.refresh(periodo_anterior)

    # Validaciones caso 2
    assert periodo_activo.fecha_inicio == date(2026, 9, 28)
    assert periodo_activo.fecha_fin == date(2026, 10, 31)
    uso_2 = uso_periodo(db_session, presu, periodo_activo, hoy=date(2026, 10, 9), usuario=usuario)
    assert uso_2.mostrado == Decimal("5000.00")
    assert uso_2.supera_limite is False

    # Período anterior no cambia
    assert periodo_anterior.fecha_inicio == date(2026, 8, 28)
    assert periodo_anterior.fecha_fin == date(2026, 9, 27)


def test_caso_3_presupuesto_semanal_y_quincenal_no_cambian(db_session, monkeypatch):
    """
    Caso 3: Presupuesto semanal y otro quincenal del mismo usuario: no cambian.
    """
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 9))

    usuario = _crear_usuario(db_session, ciclo_valor="1")
    cat = _crear_categoria(db_session)

    # Presupuesto semanal
    presu_semanal = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Salidas semanal",
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.SEMANAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    presu_semanal.categorias.append(PresupuestoCategoria(categoria_id=cat.id))
    db_session.add(presu_semanal)

    # Presupuesto quincenal
    presu_quincenal = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Combustible quincenal",
        monto=Decimal("40000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.QUINCENAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    presu_quincenal.categorias.append(PresupuestoCategoria(categoria_id=cat.id))
    db_session.add(presu_quincenal)
    db_session.commit()

    per_sem = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu_semanal.id,
        fecha_inicio=date(2026, 10, 5),
        fecha_fin=date(2026, 10, 11),
        monto_limite=Decimal("20000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    per_quin = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu_quincenal.id,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2026, 10, 15),
        monto_limite=Decimal("40000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db_session.add_all([per_sem, per_quin])
    db_session.commit()

    # Usuario cambia a día 28
    datos = EditarCicloFinanciero(ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="28")
    actualizar_ciclo_financiero(db_session, usuario, datos)

    db_session.refresh(per_sem)
    db_session.refresh(per_quin)

    # No deben cambiar
    assert per_sem.fecha_inicio == date(2026, 10, 5)
    assert per_sem.fecha_fin == date(2026, 10, 11)
    assert per_quin.fecha_inicio == date(2026, 10, 1)
    assert per_quin.fecha_fin == date(2026, 10, 15)


def test_caso_4_presupuesto_mensual_sin_periodo_activo(db_session, monkeypatch):
    """
    Caso 4: Presupuesto mensual sin período activo: no se crea nada.
    """
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 9))

    usuario = _crear_usuario(db_session, ciclo_valor="1")
    cat = _crear_categoria(db_session)

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Presupuesto sin período activo",
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    presu.categorias.append(PresupuestoCategoria(categoria_id=cat.id))
    db_session.add(presu)
    db_session.commit()

    # Período cerrado en el pasado (septiembre), hoy es 9 de octubre -> sin período activo
    per_pasado = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 9, 1),
        fecha_fin=date(2026, 9, 30),
        monto_limite=Decimal("50000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db_session.add(per_pasado)
    db_session.commit()

    # Usuario cambia a día 28
    datos = EditarCicloFinanciero(ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="28")
    actualizar_ciclo_financiero(db_session, usuario, datos)

    db_session.refresh(presu)
    # No se debe crear ningún período nuevo
    assert len(presu.periodos) == 1
    assert presu.periodos[0].id == per_pasado.id
    assert presu.periodos[0].fecha_inicio == date(2026, 9, 1)
    assert presu.periodos[0].fecha_fin == date(2026, 9, 30)


def test_caso_5_renovar_despues_del_caso_1(db_session, monkeypatch):
    """
    Caso 5: Después del caso 1, renovar_presupuestos con hoy 2026-10-28 crea el período 2026-10-28 a 2026-11-27.
    """
    # 1. Configurar estado igual al caso 1 tras el cambio a día 28
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 9))

    usuario = _crear_usuario(db_session, ciclo_valor="1")
    cat = _crear_categoria(db_session)
    _crear_transaccion_gasto(db_session, usuario.id, cat.id, fecha=date(2026, 10, 2), monto=Decimal("5000.00"))

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Comida mensual",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    presu.categorias.append(PresupuestoCategoria(categoria_id=cat.id))
    db_session.add(presu)
    db_session.commit()

    periodo_anterior = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 9, 1),
        fecha_fin=date(2026, 9, 30),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    periodo_activo = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2026, 10, 31),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("5000.00"),
        superado=False,
    )
    db_session.add_all([periodo_anterior, periodo_activo])
    db_session.commit()

    # Usuario cambia a día 28
    datos = EditarCicloFinanciero(ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="28")
    actualizar_ciclo_financiero(db_session, usuario, datos)

    # 2. Ahora simulamos que llega el día 2026-10-28
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 28))

    renovar_presupuestos(db_session)

    db_session.refresh(presu)
    # Debe haberse creado un nuevo período
    periodos = sorted(presu.periodos, key=lambda p: p.fecha_inicio)
    assert len(periodos) == 3

    nuevo_periodo = periodos[-1]
    assert nuevo_periodo.fecha_inicio == date(2026, 10, 28)
    assert nuevo_periodo.fecha_fin == date(2026, 11, 27)


def test_caso_6_cobertura_continua_sin_solapamiento_ni_huecos(db_session, monkeypatch):
    """
    Caso 6: Ningún día queda en dos períodos ni sin período: en los casos 1 y 2, el inicio del activo
    es el día siguiente al fin del anterior.
    """
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: date(2026, 10, 9))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 9))

    # --- Verificación en Caso 1 ---
    u1 = _crear_usuario(db_session, ciclo_valor="1")
    cat1 = _crear_categoria(db_session)
    p1 = Presupuesto(
        id=uuid4(),
        usuario_id=u1.id,
        nombre="Presupuesto C1",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    p1.categorias.append(PresupuestoCategoria(categoria_id=cat1.id))
    db_session.add(p1)
    db_session.commit()

    p1_ant = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p1.id,
        fecha_inicio=date(2026, 9, 1),
        fecha_fin=date(2026, 9, 30),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    p1_act = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p1.id,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2026, 10, 31),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db_session.add_all([p1_ant, p1_act])
    db_session.commit()

    actualizar_ciclo_financiero(db_session, u1, EditarCicloFinanciero(ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="28"))
    db_session.refresh(p1_ant)
    db_session.refresh(p1_act)

    # Caso 1: p1_act.fecha_inicio debe ser exactamente p1_ant.fecha_fin + 1 día
    assert p1_act.fecha_inicio == p1_ant.fecha_fin + timedelta(days=1)
    assert p1_ant.fecha_fin < p1_act.fecha_inicio

    # --- Verificación en Caso 2 ---
    u2 = _crear_usuario(db_session, ciclo_valor="28")
    cat2 = _crear_categoria(db_session)
    p2 = Presupuesto(
        id=uuid4(),
        usuario_id=u2.id,
        nombre="Presupuesto C2",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    p2.categorias.append(PresupuestoCategoria(categoria_id=cat2.id))
    db_session.add(p2)
    db_session.commit()

    p2_ant = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p2.id,
        fecha_inicio=date(2026, 8, 28),
        fecha_fin=date(2026, 9, 27),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    p2_act = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=p2.id,
        fecha_inicio=date(2026, 9, 28),
        fecha_fin=date(2026, 10, 27),
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db_session.add_all([p2_ant, p2_act])
    db_session.commit()

    actualizar_ciclo_financiero(db_session, u2, EditarCicloFinanciero(ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1"))
    db_session.refresh(p2_ant)
    db_session.refresh(p2_act)

    # Caso 2: p2_act.fecha_inicio debe ser exactamente p2_ant.fecha_fin + 1 día
    assert p2_act.fecha_inicio == p2_ant.fecha_fin + timedelta(days=1)
    assert p2_ant.fecha_fin < p2_act.fecha_inicio
