import pytest
from datetime import date, timedelta
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

# Compilación de JSONB para SQLite en memoria
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

import app.models
from app.core.database import Base
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda, CicloTipo
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion, TipoTransaccion, MetodoPago, OrigenTransaccion
from app.models.calibracion_usuario import CalibracionUsuario
from app.services.calibracion_service import (
    calcular_calibracion_usuario,
    calcular_y_guardar_calibracion_usuario,
)
from app.utils.fecha import hoy_argentina

@pytest.fixture(name="db_session")
def db_session_fixture():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()

def _crear_datos_usuario_con_historia(db_session):
    usuario = Usuario(
        id=uuid4(),
        email=f"test_calib_{uuid4().hex[:8]}@argentum.com",
        nombre="Test Calib",
        password_hash="hash",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        auth_provider=AuthProvider.EMAIL,
    )
    db_session.add(usuario)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000"),
        saldo_inicial=Decimal("500000"),
        es_principal=True,
        es_inversion=False,
    )
    db_session.add(billetera)

    categoria = Categoria(
        id=uuid4(),
        nombre="Supermercado",
        tipo=TipoCategoria.EGRESO,
    )
    db_session.add(categoria)

    subcategoria = Subcategoria(
        id=uuid4(),
        categoria_id=categoria.id,
        nombre="Almacen",
    )
    db_session.add(subcategoria)
    db_session.flush()

    hoy = hoy_argentina()

    # Generar transacciones con dispersión en al menos 8 ciclos cerrados anteriores
    for meses_atras in range(1, 9):
        f_aprox = hoy - timedelta(days=31 * meses_atras)
        fecha_tx = date(f_aprox.year, f_aprox.month, 15)
        monto_tx = Decimal(str(10000 + meses_atras * 3500))

        tx = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
            billetera_id=billetera.id,
            categoria_id=categoria.id,
            subcategoria_id=subcategoria.id,
            monto=monto_tx,
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=fecha_tx,
            descripcion=f"Gasto mes -{meses_atras}",
            metodo_pago=MetodoPago.DEBITO,
            origen=OrigenTransaccion.MANUAL,
        )
        db_session.add(tx)

    db_session.commit()
    return usuario

def test_calcular_y_guardar_calibracion_usuario_con_ctx(db_session):
    """Verifica que calcular_y_guardar_calibracion_usuario corre exitosamente con ctx y guarda la fila."""
    usuario = _crear_datos_usuario_con_historia(db_session)

    res = calcular_y_guardar_calibracion_usuario(db_session, usuario.id, Moneda.ARS)
    assert res is not None
    assert isinstance(res, CalibracionUsuario)
    assert res.usuario_id == usuario.id
    assert res.moneda == "ARS"

    # Verificar que existe 1 fila en calibraciones_usuario
    cant_filas = db_session.query(CalibracionUsuario).filter(CalibracionUsuario.usuario_id == usuario.id).count()
    assert cant_filas == 1

def test_calcular_calibracion_usuario_sin_escrituras(db_session):
    """Verifica que calcular_calibracion_usuario realiza todo el cálculo sin escribir en la base."""
    usuario = _crear_datos_usuario_con_historia(db_session)

    filas_antes = db_session.query(CalibracionUsuario).count()

    res = calcular_calibracion_usuario(db_session, usuario.id, Moneda.ARS)
    assert res is not None
    assert isinstance(res, dict)
    assert res["usuario_id"] == usuario.id
    assert res["moneda"] == "ARS"
    assert res["clasificacion"] is not None

    filas_despues = db_session.query(CalibracionUsuario).count()
    assert filas_antes == filas_despues
