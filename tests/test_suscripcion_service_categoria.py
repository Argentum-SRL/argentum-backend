import pytest
from datetime import date
from decimal import Decimal
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion
from app.schemas.suscripcion import SuscripcionCreate, SuscripcionUpdate
from app.services import suscripcion_service
from app.services.cobro_suscripcion_service import _cobrar_suscripcion


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario_billetera(db, email: str = "test@argentum.com"):
    u = Usuario(id=uuid4(), email=email, auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS)
    b = Billetera(id=uuid4(), usuario_id=u.id, nombre="Galicia", moneda=Moneda.ARS, saldo_actual=Decimal("500000.00"), estado=EstadoBilletera.ACTIVA)
    db.add_all([u, b])
    db.flush()
    return u, b


def test_actualizacion_categoria_herencia_cobros(db_session):
    u, b = _crear_usuario_billetera(db_session)
    cat_ent = Categoria(id=uuid4(), nombre="Entretenimiento", tipo=TipoCategoria.EGRESO)
    cat_sal = Categoria(id=uuid4(), nombre="Salud", tipo=TipoCategoria.EGRESO)
    db_session.add_all([cat_ent, cat_sal])
    db_session.flush()

    sub_stream = Subcategoria(id=uuid4(), categoria_id=cat_ent.id, nombre="Streaming")
    sub_dep = Subcategoria(id=uuid4(), categoria_id=cat_sal.id, nombre="Deportes y gimnasio")
    db_session.add_all([sub_stream, sub_dep])
    db_session.commit()

    sub_data = SuscripcionCreate(
        nombre="Servicio Multiuso", monto=Decimal("15000.00"), moneda="ARS", frecuencia="mensual",
        proximo_cobro=date(2026, 9, 1), vigente_desde=date(2026, 9, 1), billetera_id=b.id,
        categoria_id=cat_ent.id, subcategoria_id=sub_stream.id,
    )
    suscripcion = suscripcion_service.crear_suscripcion(db_session, u.id, sub_data)

    # Cobro período 1 (septiembre 2026)
    assert _cobrar_suscripcion(db_session, suscripcion, date(2026, 9, 1)) is True
    db_session.commit()

    txs_p1 = db_session.execute(select(Transaccion).where(Transaccion.suscripcion_id == suscripcion.id)).scalars().all()
    assert len(txs_p1) == 1
    assert txs_p1[0].categoria_id == cat_ent.id
    assert txs_p1[0].subcategoria_id == sub_stream.id

    # Actualizar suscripción a Salud / Deportes y gimnasio
    suscripcion_service.actualizar_suscripcion(db_session, u.id, suscripcion.id, SuscripcionUpdate(categoria_id=cat_sal.id, subcategoria_id=sub_dep.id))

    # Cobro período 2 (octubre 2026)
    assert _cobrar_suscripcion(db_session, suscripcion, date(2026, 10, 1)) is True
    db_session.commit()

    txs = db_session.execute(select(Transaccion).where(Transaccion.suscripcion_id == suscripcion.id).order_by(Transaccion.fecha.asc())).scalars().all()
    assert len(txs) == 2
    # El cobro pasado conserva categoría anterior
    assert txs[0].categoria_id == cat_ent.id and txs[0].subcategoria_id == sub_stream.id
    # El nuevo cobro hereda categoría actualizada
    assert txs[1].categoria_id == cat_sal.id and txs[1].subcategoria_id == sub_dep.id


def test_validacion_categoria_no_egreso_error_400(db_session):
    u, b = _crear_usuario_billetera(db_session, "test_cat_ing@argentum.com")
    cat_ing = Categoria(id=uuid4(), nombre="Sueldo", tipo=TipoCategoria.INGRESO)
    cat_egr = Categoria(id=uuid4(), nombre="Servicios", tipo=TipoCategoria.EGRESO)
    db_session.add_all([cat_ing, cat_egr])
    db_session.commit()

    # Creación con ingreso -> 400
    sub_data = SuscripcionCreate(nombre="Invalida", monto=Decimal("5000.00"), frecuencia="mensual", proximo_cobro=date(2026, 9, 1), billetera_id=b.id, categoria_id=cat_ing.id)
    with pytest.raises(HTTPException) as exc_info:
        suscripcion_service.crear_suscripcion(db_session, u.id, sub_data)
    assert exc_info.value.status_code == 400 and "no es de egreso" in exc_info.value.detail

    # Actualización con ingreso -> 400
    sub_val = SuscripcionCreate(nombre="Valida", monto=Decimal("5000.00"), frecuencia="mensual", proximo_cobro=date(2026, 9, 1), billetera_id=b.id, categoria_id=cat_egr.id)
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, sub_val)
    with pytest.raises(HTTPException) as exc_info:
        suscripcion_service.actualizar_suscripcion(db_session, u.id, sub.id, SuscripcionUpdate(categoria_id=cat_ing.id))
    assert exc_info.value.status_code == 400 and "no es de egreso" in exc_info.value.detail


def test_validacion_subcategoria_no_pertenece_error_400(db_session):
    u, b = _crear_usuario_billetera(db_session, "test_sub_invalida@argentum.com")
    cat_a = Categoria(id=uuid4(), nombre="Salud", tipo=TipoCategoria.EGRESO)
    cat_b = Categoria(id=uuid4(), nombre="Vivienda", tipo=TipoCategoria.EGRESO)
    db_session.add_all([cat_a, cat_b])
    db_session.flush()

    sub_b = Subcategoria(id=uuid4(), categoria_id=cat_b.id, nombre="Alquiler")
    db_session.add(sub_b)
    db_session.commit()

    # Creación con subcategoría ajena -> 400
    sub_data = SuscripcionCreate(nombre="Gimnasio", monto=Decimal("5000.00"), frecuencia="mensual", proximo_cobro=date(2026, 9, 1), billetera_id=b.id, categoria_id=cat_a.id, subcategoria_id=sub_b.id)
    with pytest.raises(HTTPException) as exc_info:
        suscripcion_service.crear_suscripcion(db_session, u.id, sub_data)
    assert exc_info.value.status_code == 400 and "no pertenece a la categoría" in exc_info.value.detail

    # Actualización con subcategoría ajena -> 400
    sub_val = SuscripcionCreate(nombre="Gimnasio", monto=Decimal("5000.00"), frecuencia="mensual", proximo_cobro=date(2026, 9, 1), billetera_id=b.id, categoria_id=cat_a.id)
    sub = suscripcion_service.crear_suscripcion(db_session, u.id, sub_val)
    with pytest.raises(HTTPException) as exc_info:
        suscripcion_service.actualizar_suscripcion(db_session, u.id, sub.id, SuscripcionUpdate(subcategoria_id=sub_b.id))
    assert exc_info.value.status_code == 400 and "no pertenece a la categoría" in exc_info.value.detail


def test_obtener_suscripciones_incluye_relaciones_categoria(db_session):
    u, b = _crear_usuario_billetera(db_session, "test_relaciones@argentum.com")
    cat = Categoria(id=uuid4(), nombre="Salud", tipo=TipoCategoria.EGRESO)
    db_session.add(cat)
    db_session.flush()

    subcat = Subcategoria(id=uuid4(), categoria_id=cat.id, nombre="Deportes y gimnasio")
    db_session.add(subcat)
    db_session.commit()

    sub_data = SuscripcionCreate(
        nombre="Gimnasio",
        monto=Decimal("22.00"),
        frecuencia="mensual",
        proximo_cobro=date(2026, 10, 9),
        billetera_id=b.id,
        categoria_id=cat.id,
        subcategoria_id=subcat.id,
    )
    suscripcion = suscripcion_service.crear_suscripcion(db_session, u.id, sub_data)

    suscripciones = suscripcion_service.obtener_suscripciones(db_session, u.id)
    assert len(suscripciones) == 1
    assert suscripciones[0].subcategoria is not None
    assert suscripciones[0].subcategoria.nombre == "Deportes y gimnasio"
    assert suscripciones[0].categoria is not None
    assert suscripciones[0].categoria.nombre == "Salud"

    detalle = suscripcion_service.obtener_suscripcion_detalle(db_session, u.id, suscripcion.id)
    assert detalle.subcategoria is not None
    assert detalle.subcategoria.nombre == "Deportes y gimnasio"
    assert detalle.categoria is not None
    assert detalle.categoria.nombre == "Salud"

