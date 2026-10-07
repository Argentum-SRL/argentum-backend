from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import uuid
from uuid import UUID, uuid4
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base


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


from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.factura import Factura
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import dashboard_service, factura_service, transaccion_service
from app.services.factura_avisos_service import _job_notificaciones_facturas
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
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


@pytest.fixture
def fixtures_comunes(db_session: Session):
    usuario = Usuario(
        id=uuid4(),
        email="test_facturas@ejemplo.com",
        password_hash="hash",
        nombre="Test Facturas",
        moneda_principal=Moneda.ARS,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        email_verificado=True,
    )
    db_session.add(usuario)

    billetera_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Efectivo ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000000.00"),
        saldo_inicial=Decimal("1000000.00"),
        es_efectivo=True,
        estado=EstadoBilletera.ACTIVA,
    )
    billetera_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Caja USD",
        moneda=Moneda.USD,
        saldo_actual=Decimal("1000.00"),
        saldo_inicial=Decimal("1000.00"),
        es_efectivo=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(billetera_ars)
    db_session.add(billetera_usd)

    cat_vivienda = Categoria(
        id=uuid4(),
        nombre="Vivienda",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
        icono="home",
        color="#112233",
    )
    db_session.add(cat_vivienda)
    db_session.flush()

    sub_luz = Subcategoria(
        id=uuid4(),
        categoria_id=cat_vivienda.id,
        nombre="Luz",
        estado=EstadoSubcategoria.ACTIVA,
    )
    db_session.add(sub_luz)

    cat_super = Categoria(
        id=uuid4(),
        nombre="Supermercado",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
        icono="cart",
        color="#445566",
    )
    db_session.add(cat_super)

    cat_otros = Categoria(
        id=uuid4(),
        nombre="Otros",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
        icono="dots",
        color="#778899",
    )
    db_session.add(cat_otros)

    db_session.commit()

    return {
        "usuario": usuario,
        "billetera_ars": billetera_ars,
        "billetera_usd": billetera_usd,
        "cat_vivienda": cat_vivienda,
        "sub_luz": sub_luz,
        "cat_super": cat_super,
        "cat_otros": cat_otros,
    }


def test_1_crear_factura(db_session: Session, fixtures_comunes):
    f = fixtures_comunes
    hoy = hoy_argentina()

    # Factura base: "EPE", 45678.90 ARS, en Vivienda > Luz, que vence en hoy + 5 y llegó hoy.
    fac1 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("45678.90"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    assert fac1.id is not None
    assert getattr(fac1, "ya_existia", False) is False

    # Repetir el mismo monto y vencimiento devuelve la misma factura con ya_existia = True
    fac2 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Repetida",
        monto=Decimal("45678.90"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    assert fac2.id == fac1.id
    assert getattr(fac2, "ya_existia", False) is True

    # Monto 0 da error
    with pytest.raises(ValueError):
        factura_service.crear_factura_pendiente(
            db=db_session,
            usuario_id=f["usuario"].id,
            descripcion="Monto Cero",
            monto=Decimal("0"),
            moneda="ARS",
            fecha_vencimiento=hoy + timedelta(days=5),
            origen="whatsapp_foto",
            commit=True,
        )


def test_2_marcado_automatico(db_session: Session, fixtures_comunes):
    f = fixtures_comunes
    hoy = hoy_argentina()

    # Helper para crear factura base
    def crear_base(vencimiento_offset=5):
        return factura_service.crear_factura_pendiente(
            db=db_session,
            usuario_id=f["usuario"].id,
            descripcion="EPE",
            monto=Decimal("45678.90"),
            moneda="ARS",
            fecha_vencimiento=hoy + timedelta(days=vencimiento_offset),
            origen="whatsapp_foto",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            commit=True,
        )

    # 2.a Gasto de 45678.90 en Luz con fecha de hoy: queda pagada, automática, con su transaccion_id
    fac_a = crear_base()
    tx_a = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("45678.90"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="Pago de la luz",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_a)
    assert fac_a.estado == "pagada"
    assert fac_a.pagada_automaticamente is True
    assert fac_a.transaccion_id == tx_a.id

    # 2.b Mismo monto en Supermercado con descripción "Coto": no se marca
    fac_b = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Gas",
        monto=Decimal("12345.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_b = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("12345.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="Coto compras",
            categoria_id=f["cat_super"].id,
            subcategoria_id=None,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_b)
    assert fac_b.estado == "pendiente"
    assert fac_b.transaccion_id is None

    # 2.c Mismo monto en categoría Otros con descripción "EPE": se marca (criterio clave_comercio)
    fac_c = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("22222.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_c = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("22222.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="EPE",
            categoria_id=f["cat_otros"].id,
            subcategoria_id=None,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_c)
    assert fac_c.estado == "pagada"
    assert fac_c.pagada_automaticamente is True
    assert fac_c.transaccion_id == tx_c.id

    # 2.d Gasto de 45678.00: no se marca
    fac_d = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("45678.90"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=20),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_d = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("45678.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="EPE",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_d)
    assert fac_d.estado == "pendiente"

    # 2.e Gasto con fecha en vencimiento + 10: se marca. Con vencimiento + 11: no.
    fac_e1 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("33333.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_e1 = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("33333.00"),
            moneda=Moneda.ARS,
            fecha=fac_e1.fecha_vencimiento + timedelta(days=10),
            descripcion="EPE",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_e1)
    assert fac_e1.estado == "pagada"
    assert fac_e1.transaccion_id == tx_e1.id

    fac_e2 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("33334.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_e2 = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("33334.00"),
            moneda=Moneda.ARS,
            fecha=fac_e2.fecha_vencimiento + timedelta(days=11),
            descripcion="EPE",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_e2)
    assert fac_e2.estado == "pendiente"

    # 2.f Gasto con fecha de ayer, anterior a la llegada de la factura: no se marca
    fac_f = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("44444.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_f = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("44444.00"),
            moneda=Moneda.ARS,
            fecha=hoy - timedelta(days=1),
            descripcion="EPE",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_f)
    assert fac_f.estado == "pendiente"

    # 2.g Dos facturas pendientes iguales en monto y categoría (con vencimientos distintos): no se marca ninguna
    fac_g1 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("55555.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=3),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    fac_g2 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("55555.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=7),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_g = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("55555.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="EPE",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_g1)
    db_session.refresh(fac_g2)
    assert fac_g1.estado == "pendiente"
    assert fac_g2.estado == "pendiente"

    # 2.h Un ingreso del mismo monto: no se marca
    fac_h = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("66666.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_h = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("66666.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="EPE Ingreso",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_h)
    assert fac_h.estado == "pendiente"

    # 2.i Un gasto en USD: no se marca
    fac_i = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("77777.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx_i = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("77777.00"),
            moneda=Moneda.USD,
            fecha=hoy,
            descripcion="EPE en dolares",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_usd"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_i)
    assert fac_i.estado == "pendiente"

    # 2.j Factura y gasto en Otros con descripciones "EPE" y "Coto": no se marca
    fac_j = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("88888.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_otros"].id,
        subcategoria_id=None,
        commit=True,
    )
    tx_j = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("88888.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="Coto compra",
            categoria_id=f["cat_otros"].id,
            subcategoria_id=None,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac_j)
    assert fac_j.estado == "pendiente"


def test_3_factura_id_explicito(db_session: Session, fixtures_comunes):
    f = fixtures_comunes
    hoy = hoy_argentina()

    # Factura a pagar
    fac = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="Factura Manual",
        monto=Decimal("50000.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )

    # Con monto distinto, marca esa factura con automática = False
    tx = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("49999.00"),  # monto diferente
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="Pago parcial o diferente",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
            factura_id=fac.id,
        ),
        commit=True,
    )
    db_session.refresh(fac)
    assert fac.estado == "pagada"
    assert fac.pagada_automaticamente is False
    assert fac.transaccion_id == tx.id

    # Con factura de otro usuario: 404 con texto exacto
    otro_usuario_id = uuid4()
    fac_otro = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=otro_usuario_id,
        descripcion="Factura de Otro",
        monto=Decimal("1000.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    with pytest.raises(HTTPException) as exc_info_404:
        transaccion_service.crear_transaccion(
            db=db_session,
            usuario_id=f["usuario"].id,
            data=TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("1000.00"),
                moneda=Moneda.ARS,
                fecha=hoy,
                descripcion="Intento pagar factura ajena",
                categoria_id=f["cat_vivienda"].id,
                subcategoria_id=f["sub_luz"].id,
                metodo_pago=MetodoPago.EFECTIVO,
                billetera_id=f["billetera_ars"].id,
                factura_id=fac_otro.id,
            ),
            commit=True,
        )
    assert exc_info_404.value.status_code == 404
    assert exc_info_404.value.detail == "No encontré esa factura."

    # Con factura ya pagada: 400 con texto exacto
    with pytest.raises(HTTPException) as exc_info_400:
        transaccion_service.crear_transaccion(
            db=db_session,
            usuario_id=f["usuario"].id,
            data=TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("1000.00"),
                moneda=Moneda.ARS,
                fecha=hoy,
                descripcion="Intento pagar factura ya pagada",
                categoria_id=f["cat_vivienda"].id,
                subcategoria_id=f["sub_luz"].id,
                metodo_pago=MetodoPago.EFECTIVO,
                billetera_id=f["billetera_ars"].id,
                factura_id=fac.id,
            ),
            commit=True,
        )
    assert exc_info_400.value.status_code == 400
    assert exc_info_400.value.detail == "Esa factura ya no está pendiente."


def test_4_eliminar_transaccion_reversion(db_session: Session, fixtures_comunes):
    f = fixtures_comunes
    hoy = hoy_argentina()

    fac = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE",
        monto=Decimal("15000.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    tx = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("15000.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="Pago de la luz EPE",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=True,
    )
    db_session.refresh(fac)
    assert fac.estado == "pagada"
    assert fac.transaccion_id == tx.id

    # Eliminar la transacción devuelve la factura a pendiente
    transaccion_service.eliminar_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        transaccion_id=tx.id,
        commit=True,
    )
    db_session.refresh(fac)
    assert fac.estado == "pendiente"
    assert fac.transaccion_id is None
    assert fac.pagada_automaticamente is False


def test_5_desmarcar_y_descartar(db_session: Session, fixtures_comunes):
    f = fixtures_comunes
    hoy = hoy_argentina()

    fac = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="Factura Desmarcar",
        monto=Decimal("10000.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )

    # Desmarcar una factura pendiente da 400
    with pytest.raises(HTTPException) as exc_desm:
        factura_service.desmarcar(db_session, fac.id, f["usuario"].id)
    assert exc_desm.value.status_code == 400
    assert exc_desm.value.detail == "Esa factura no está marcada como pagada."

    # Marcar pagada manualmente
    fac = factura_service.marcar_pagada(
        db_session, fac.id, f["usuario"].id, pagada_automaticamente=True
    )
    assert fac.estado == "pagada"

    # Desmarcar factura pagada la devuelve a pendiente
    fac = factura_service.desmarcar(db_session, fac.id, f["usuario"].id)
    assert fac.estado == "pendiente"
    assert fac.transaccion_id is None
    assert fac.pagada_automaticamente is False

    # Descartar factura pendiente la pasa a descartada
    fac = factura_service.descartar(db_session, fac.id, f["usuario"].id)
    assert fac.estado == "descartada"

    # Descartar una factura ya descartada da 400
    with pytest.raises(HTTPException) as exc_desc:
        factura_service.descartar(db_session, fac.id, f["usuario"].id)
    assert exc_desc.value.status_code == 400
    assert exc_desc.value.detail == "Solo podés descartar facturas pendientes."


def test_6_atomicidad_operaciones(db_session: Session, fixtures_comunes):
    f = fixtures_comunes
    hoy = hoy_argentina()

    fac = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Atomicidad",
        monto=Decimal("9999.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )

    # Crear transacción con commit=False simulando fallo posterior
    tx = transaccion_service.crear_transaccion(
        db=db_session,
        usuario_id=f["usuario"].id,
        data=TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("9999.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="EPE Atomicidad",
            categoria_id=f["cat_vivienda"].id,
            subcategoria_id=f["sub_luz"].id,
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=f["billetera_ars"].id,
        ),
        commit=False,
    )
    # En la misma sesión flush, la factura está temporalmente pagada
    assert fac.estado == "pagada"

    # Rollback de la transacción externa
    db_session.rollback()

    # Después del rollback, la factura sigue pendiente
    db_session.refresh(fac)
    assert fac.estado == "pendiente"
    assert fac.transaccion_id is None


def test_7_avisos_job(db_session: Session, fixtures_comunes, monkeypatch):
    f = fixtures_comunes
    hoy = hoy_argentina()

    # Factura 1: vence en hoy + 3
    fac_3 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Vence 3",
        monto=Decimal("45678.90"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=3),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )

    # Factura 2: vence hoy
    fac_hoy = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Vence Hoy",
        monto=Decimal("45678.90"),
        moneda="ARS",
        fecha_vencimiento=hoy,
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )

    # Facturas que NO deben generar aviso: pagada, descartada, vence en hoy + 2
    fac_pagada = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Pagada",
        monto=Decimal("11111.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=3),
        origen="whatsapp_foto",
        commit=True,
    )
    factura_service.marcar_pagada(db_session, fac_pagada.id, f["usuario"].id, commit=True)

    fac_descartada = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Descartada",
        monto=Decimal("22222.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=3),
        origen="whatsapp_foto",
        commit=True,
    )
    factura_service.descartar(db_session, fac_descartada.id, f["usuario"].id, commit=True)

    fac_2 = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="EPE Vence 2",
        monto=Decimal("33333.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=2),
        origen="whatsapp_foto",
        commit=True,
    )

    fac_3_id = fac_3.id
    dd_mm = f"{fac_3.fecha_vencimiento.day:02d}/{fac_3.fecha_vencimiento.month:02d}"

    class TestSessionProxy:
        def __init__(self, s):
            self._s = s
        def close(self):
            pass
        def __getattr__(self, name):
            return getattr(self._s, name)

    # Correr el job directo
    _job_notificaciones_facturas(lambda: TestSessionProxy(db_session))

    # Verificar notificaciones creadas
    notifs = db_session.query(Notificacion).filter(
        Notificacion.usuario_id == f["usuario"].id,
        Notificacion.tipo == TipoNotificacion.FACTURA_VENCE
    ).all()
    assert len(notifs) == 2

    monto_fmt = formatear_monto(Decimal("45678.90"), "ARS")
    esperado_3 = f"La factura de EPE Vence 3 por {monto_fmt} vence en 3 días ({dd_mm})."
    esperado_hoy = f"La factura de EPE Vence Hoy por {monto_fmt} vence hoy."

    mensajes = [n.mensaje for n in notifs]
    assert esperado_3 in mensajes
    assert esperado_hoy in mensajes

    # Verificar parámetros de la notificación
    notif_3 = next(n for n in notifs if n.mensaje == esperado_3)
    assert notif_3.nivel == NivelNotificacion.FINANCIERA_IMPORTANTE
    assert notif_3.canal_web is True
    assert notif_3.canal_whatsapp is False
    assert notif_3.canal_email is False
    assert notif_3.entidad_tipo == "factura"
    assert notif_3.entidad_id == fac_3_id
    assert notif_3.deep_link == "/app/dashboard"
    assert notif_3.grupo_agrupacion == f"FACTURA_VENCE_{fac_3_id}_{hoy:%Y%m%d}"

    # Correr el job por segunda vez el mismo día deja 1 sola notificación de cada una
    _job_notificaciones_facturas(lambda: TestSessionProxy(db_session))
    notifs_segunda = db_session.query(Notificacion).filter(
        Notificacion.usuario_id == f["usuario"].id,
        Notificacion.tipo == TipoNotificacion.FACTURA_VENCE
    ).all()
    assert len(notifs_segunda) == 2


def test_8_proximos_pagos_dashboard(db_session: Session, fixtures_comunes):
    f = fixtures_comunes
    hoy = hoy_argentina()

    # 1. Pendiente que vence en 5 días
    fac_pend = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="Luz Pendiente",
        monto=Decimal("12300.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )

    # 2. Vencida hace 10 días
    fac_vencida = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="Luz Vencida",
        monto=Decimal("15000.00"),
        moneda="ARS",
        fecha_vencimiento=hoy - timedelta(days=10),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )

    # 3. Pagada automáticamente con vencimiento futuro (+4 días)
    fac_pagada_auto = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="Luz Pagada Auto",
        monto=Decimal("18000.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=4),
        origen="whatsapp_foto",
        categoria_id=f["cat_vivienda"].id,
        subcategoria_id=f["sub_luz"].id,
        commit=True,
    )
    factura_service.marcar_pagada(
        db=db_session,
        factura_id=fac_pagada_auto.id,
        usuario_id=f["usuario"].id,
        pagada_automaticamente=True,
        commit=True,
    )

    # 4. Descartada (no debe aparecer)
    fac_desc = factura_service.crear_factura_pendiente(
        db=db_session,
        usuario_id=f["usuario"].id,
        descripcion="Luz Descartada",
        monto=Decimal("99999.00"),
        moneda="ARS",
        fecha_vencimiento=hoy + timedelta(days=5),
        origen="whatsapp_foto",
        commit=True,
    )
    factura_service.descartar(db_session, fac_desc.id, f["usuario"].id, commit=True)

    res = dashboard_service.get_dashboard_resumen(db_session, f["usuario"])
    proximos_pagos = res["proximos_pagos"]

    pagos_facturas = [p for p in proximos_pagos if p.get("tipo") == "factura"]
    assert len(pagos_facturas) == 3

    # Pendiente
    p_pend = next(p for p in pagos_facturas if p["factura_id"] == str(fac_pend.id))
    assert p_pend["nombre"] == "Factura de Luz Pendiente"
    assert p_pend["fecha_cobro"] == (hoy + timedelta(days=5)).isoformat()
    assert p_pend["monto"] == 12300.00
    assert p_pend["estado_factura"] == "pendiente"

    # Vencida hace 10 días
    p_venc = next(p for p in pagos_facturas if p["factura_id"] == str(fac_vencida.id))
    assert p_venc["nombre"] == "Factura de Luz Vencida"
    assert p_venc["estado_factura"] == "vencida"
    assert p_venc["es_vencido"] is True

    # Pagada automáticamente futura
    p_pagada = next(p for p in pagos_facturas if p["factura_id"] == str(fac_pagada_auto.id))
    assert p_pagada["nombre"] == "Factura de Luz Pagada Auto"
    assert p_pagada["estado_factura"] == "pagada"

    # Descartada no aparece
    assert not any(p["factura_id"] == str(fac_desc.id) for p in proximos_pagos)
