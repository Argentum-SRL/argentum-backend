"""
tests/test_bloque_f_devoluciones.py — Pruebas para TF.1, TF.2, TF.3, TF.3b, Grupo, Dashboard y Convivencia.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import (
    cuotas_service,
    dashboard_service,
    historial_service,
    que_es_plata_service,
    reintegro_service,
    transaccion_service,
)
from app.services.conciliacion_service import calcular_saldo_teorico
from app.services.definiciones_service import condicion_gasto
from app.routers.grupos_cuotas import mapear_grupo_resumen


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
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


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def do_connect(dbapi_connection, connection_record):
        def _sqlite_translate(val, from_chars, to_chars):
            if val is None:
                return None
            return str(val).translate(str.maketrans(from_chars, to_chars))
        dbapi_connection.create_function("translate", 3, _sqlite_translate)

    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(name="usuario")
def usuario_fixture(db: Session):
    user = Usuario(
        id=uuid4(),
        email="test_devoluciones@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="billetera_ars")
def billetera_ars_fixture(db: Session, usuario: Usuario):
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Principal",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        fecha_saldo=date(2026, 9, 1),
        es_inversion=False,
    )
    db.add(billetera)
    db.commit()
    db.refresh(billetera)
    return billetera


@pytest.fixture(name="tarjeta_credito")
def tarjeta_fixture(db: Session, usuario: Usuario, billetera_ars: Billetera):
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Visa Crédito",
        limite_credito=Decimal("2000000.00"),
        dia_cierre=22,
        dia_vencimiento=4,
        billetera_id=billetera_ars.id,
        red=RedTarjeta.VISA,
    )
    db.add(tarjeta)
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


@pytest.fixture(name="categorias")
def categorias_fixture(db: Session):
    cat_hogar = Categoria(id=uuid4(), nombre="Hogar", tipo=TipoCategoria.EGRESO)
    cat_super = Categoria(id=uuid4(), nombre="Supermercado", tipo=TipoCategoria.EGRESO)
    cat_otros_egr = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    cat_otros_ing = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.INGRESO)
    db.add_all([cat_hogar, cat_super, cat_otros_egr, cat_otros_ing])
    db.flush()

    sub_paso_egr = Subcategoria(id=uuid4(), categoria_id=cat_otros_egr.id, nombre="Pasó por mi cuenta", orden=4)
    sub_cobros = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Cobros de personas", orden=1)
    sub_paso_ing = Subcategoria(id=uuid4(), categoria_id=cat_otros_ing.id, nombre="Pasó por mi cuenta", orden=3)
    db.add_all([sub_paso_egr, sub_cobros, sub_paso_ing])
    db.commit()

    return {
        "hogar": cat_hogar,
        "supermercado": cat_super,
        "otros_egreso": cat_otros_egr,
        "otros_ingreso": cat_otros_ing,
        "sub_paso_egr": sub_paso_egr,
        "sub_cobros": sub_cobros,
        "sub_paso_ing": sub_paso_ing,
    }


def test_tf1_devolucion_total_debito(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    TF.1: compra "Silla" de 50.000 en débito el 05/10/2026 en Hogar y devolución de 50.000 recibida el 08/10:
    saldo 1000000.00 e igual al teórico; gasto de Hogar del 01/10 al 31/10 (hoy 21/10) 0.00;
    listar_movimientos del 01/10 al 31/10 con hoy 21/10: 2 filas, primero "Reintegro: Silla"
    (etiqueta "Reintegro", sentido "entra", monto 50000.00) y después "Silla"
    (sentido "sale", monto 50000.00, reintegro.estado "recibido", reintegro.monto 50000.00);
    totales gastos 0.00.
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Silla",
        categoria_id=categorias["hogar"].id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("50000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 8),
    )

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("1000000.00")
    teorico = calcular_saldo_teorico(db, billetera_ars.id)
    assert teorico == billetera_ars.saldo_actual

    # Gasto de Hogar del 01/10 al 31/10 con hoy 21/10: 0.00
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21)),
        Transaccion.categoria_id == categorias["hogar"].id,
    )
    txs_gasto = db.execute(stmt_gasto).scalars().all()
    total_gasto = sum(t.monto for t in txs_gasto)
    assert total_gasto == Decimal("0.00")

    # listar_movimientos
    resp = historial_service.listar_movimientos(
        db=db,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        hoy=date(2026, 10, 21),
    )
    assert len(resp.items) == 2
    f0 = resp.items[0]
    assert f0.descripcion == "Reintegro: Silla"
    assert f0.etiqueta == "Reintegro"
    assert f0.sentido == "entra"
    assert f0.monto == Decimal("50000.00")

    f1 = resp.items[1]
    assert f1.descripcion == "Silla"
    assert f1.sentido == "sale"
    assert f1.monto == Decimal("50000.00")
    assert f1.reintegro is not None
    assert f1.reintegro.estado == "recibido"
    assert f1.reintegro.monto == Decimal("50000.00")

    assert resp.totales["ARS"].gastos == Decimal("0.00")


def test_tf2_devolucion_parcial_debito(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    TF.2: igual con devolución de 20.000: saldo 980000.00 y gasto 30000.00.
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Silla",
        categoria_id=categorias["hogar"].id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("20000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 8),
    )

    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == Decimal("970000.00")

    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21)),
        Transaccion.categoria_id == categorias["hogar"].id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("30000.00")


def test_tf3_devolucion_credito_cuotas(db: Session, usuario: Usuario, billetera_ars: Billetera, tarjeta_credito: TarjetaCredito, categorias: dict):
    """
    TF.3: compra "Tele" de 30.000 con la tarjeta en 3 cuotas sin interés el 05/10/2026
    (cuotas con vencimiento 04/11/2026, 04/12/2026 y 04/01/2027, de 10.000);
    pago confirmado del resumen 04/11 y la cuota 1 marcada pagada (como en test_caso_d);
    devolución de 30.000 recibida el 10/11/2026 sobre la transacción padre:
    hija del espejo con fecha 04/12/2026 y monto −30000.00 y su Cuota con monto_proyectado −30000.00;
    las cuotas 2 y 3 siguen en 10000.00, sin pagar, con vencimiento 04/12/2026 y 04/01/2027;
    suma de las cuotas de la tarjeta con vencimiento 04/12/2026: −20000.00;
    gasto de la categoría del 01/10/2026 al 31/01/2027 con hoy 31/01/2027: 0.00;
    del 01/11 al 30/11 con hoy 30/11: −20000.00; el saldo de la billetera no cambia.
    Grupo: mapear_grupo_resumen del grupo de TF.3 -> reintegro {"estado": "recibido", "monto": 30000.00, "fecha_recibido": 2026-11-10};
    sin reintegro -> None.
    """
    saldo_inicial_billetera = billetera_ars.saldo_actual

    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Tele",
        categoria_id=categorias["hogar"].id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta_credito.id,
        billetera_id=billetera_ars.id,
        info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("30000.00")),
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    # Pago confirmado del resumen 04/11
    pago_resumen = Transaccion(
        usuario_id=usuario.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 25),
        descripcion="Pago resumen 04/11",
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta_credito.id,
        pago_resumen_vencimiento=date(2026, 11, 4),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(pago_resumen)

    grupo_compra = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.transaccion_padre_id == compra.id)
    ).scalar_one()

    # Cuota 1 marcada pagada
    cuotas = db.execute(
        select(Cuota).where(Cuota.grupo_id == grupo_compra.id).order_by(Cuota.numero_cuota)
    ).scalars().all()
    assert len(cuotas) == 3
    cuotas[0].pagada = True
    db.commit()

    # Devolución de 30.000 recibida el 10/11/2026 sobre la transacción padre
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("30000.00"),
        recibido=True,
        fecha_recibido=date(2026, 11, 10),
    )

    # Hija del espejo con fecha 04/12/2026 y monto -30000.00 y su Cuota con monto_proyectado -30000.00
    hija_espejo = db.execute(
        select(Transaccion).where(
            Transaccion.reintegro_de_id == compra.id,
            Transaccion.es_cuota_hija.is_(True),
        )
    ).scalar_one()
    assert hija_espejo.fecha == date(2026, 12, 4)
    assert hija_espejo.monto == Decimal("-30000.00")

    cuota_espejo = db.execute(
        select(Cuota).where(Cuota.transaccion_id == hija_espejo.id)
    ).scalar_one()
    assert cuota_espejo.monto_proyectado == Decimal("-30000.00")
    assert cuota_espejo.fecha_vencimiento == date(2026, 12, 4)

    # Las cuotas 2 y 3 siguen en 10000.00, sin pagar, con vencimiento 04/12/2026 y 04/01/2027
    db.refresh(cuotas[1])
    db.refresh(cuotas[2])
    assert cuotas[1].monto_proyectado == Decimal("10000.00")
    assert cuotas[1].pagada is False
    assert cuotas[1].fecha_vencimiento == date(2026, 12, 4)
    assert cuotas[2].monto_proyectado == Decimal("10000.00")
    assert cuotas[2].pagada is False
    assert cuotas[2].fecha_vencimiento == date(2027, 1, 4)

    # Suma de las cuotas de la tarjeta con vencimiento 04/12/2026: -20000.00
    cuotas_0412 = db.execute(
        select(Cuota).join(GrupoCuotas).where(
            GrupoCuotas.tarjeta_id == tarjeta_credito.id,
            Cuota.fecha_vencimiento == date(2026, 12, 4),
        )
    ).scalars().all()
    suma_0412 = sum(c.monto_proyectado for c in cuotas_0412)
    assert suma_0412 == Decimal("-20000.00")

    # Gasto de la categoría del 01/10/2026 al 31/01/2027 con hoy 31/01/2027: 0.00
    stmt_gasto_total = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2027, 1, 31), hoy=date(2027, 1, 31)),
        Transaccion.categoria_id == categorias["hogar"].id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto_total).scalars().all())
    assert total_gasto == Decimal("0.00")

    # Gasto del 01/11 al 30/11 con hoy 30/11: -20000.00
    stmt_gasto_nov = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 11, 1), hasta=date(2026, 11, 30), hoy=date(2026, 11, 30)),
        Transaccion.categoria_id == categorias["hogar"].id,
    )
    gasto_nov = sum(t.monto for t in db.execute(stmt_gasto_nov).scalars().all())
    assert gasto_nov == Decimal("-20000.00")

    # El saldo de la billetera no cambia
    db.refresh(billetera_ars)
    assert billetera_ars.saldo_actual == saldo_inicial_billetera

    # Grupo: mapear_grupo_resumen
    db.refresh(grupo_compra)
    db.refresh(compra)
    resumen_grupo = mapear_grupo_resumen(db, grupo_compra)
    assert resumen_grupo["reintegro"] is not None
    assert resumen_grupo["reintegro"]["estado"] == "recibido"
    assert resumen_grupo["reintegro"]["monto"] == Decimal("30000.00")
    assert resumen_grupo["reintegro"]["fecha_recibido"] in (date(2026, 11, 10), "2026-11-10")

    # Grupo sin reintegro -> None
    compra_sin_reint_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("12000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Radio",
        categoria_id=categorias["hogar"].id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta_credito.id,
        billetera_id=billetera_ars.id,
        info_cuotas=InfoCuotas(cantidad_cuotas=2, monto_total=Decimal("12000.00")),
    )
    compra_sin_reint = transaccion_service.crear_transaccion(db, usuario.id, compra_sin_reint_data)
    grupo_sin_reint = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.transaccion_padre_id == compra_sin_reint.id)
    ).scalar_one()
    resumen_sin_reint = mapear_grupo_resumen(db, grupo_sin_reint)
    assert resumen_sin_reint["reintegro"] is None


def test_tf3b_devolucion_parcial_y_cancelar_grupo(db: Session, usuario: Usuario, billetera_ars: Billetera, tarjeta_credito: TarjetaCredito, categorias: dict):
    """
    TF.3b: mismo armado con devolución de 10.000 y después cuotas_service.cancelar_grupo del grupo de la compra:
    cuotas 2 y 3 con monto_proyectado 0.00 y pagada True; gasto del 01/10/2026 al 31/01/2027 0.00.
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Tele",
        categoria_id=categorias["hogar"].id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta_credito.id,
        billetera_id=billetera_ars.id,
        info_cuotas=InfoCuotas(cantidad_cuotas=3, monto_total=Decimal("30000.00")),
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    pago_resumen = Transaccion(
        usuario_id=usuario.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 25),
        descripcion="Pago resumen 04/11",
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta_credito.id,
        pago_resumen_vencimiento=date(2026, 11, 4),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(pago_resumen)

    grupo_compra = db.execute(
        select(GrupoCuotas).where(GrupoCuotas.transaccion_padre_id == compra.id)
    ).scalar_one()

    cuotas = db.execute(
        select(Cuota).where(Cuota.grupo_id == grupo_compra.id).order_by(Cuota.numero_cuota)
    ).scalars().all()
    cuotas[0].pagada = True
    db.commit()

    # Devolución de 10.000 recibida el 10/11/2026
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("10000.00"),
        recibido=True,
        fecha_recibido=date(2026, 11, 10),
    )

    # Cancelar grupo
    cuotas_service.cancelar_grupo(db, grupo_compra.id, usuario.id)

    db.refresh(cuotas[1])
    db.refresh(cuotas[2])
    assert cuotas[1].monto_proyectado == Decimal("0.00")
    assert cuotas[1].pagada is True
    assert cuotas[2].monto_proyectado == Decimal("0.00")
    assert cuotas[2].pagada is True

    # Gasto del 01/10/2026 al 31/01/2027 0.00
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2027, 1, 31), hoy=date(2027, 1, 31)),
        Transaccion.categoria_id == categorias["hogar"].id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("0.00")


def test_dashboard_movimientos(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    Dashboard (modelo test_caso_i_get_dashboard_resumen):
    compra "Silla" 50.000 el 05/10 y devolución de 20.000 recibida el 10/10, hoy 21/10:
    ultimos_movimientos trae "Reintegro: Silla" con monto 20000.0 y sentido "entra"
    y "Silla" con monto 50000.0 y sentido "sale".
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Silla",
        categoria_id=categorias["hogar"].id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("20000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 10),
    )

    resumen = dashboard_service.get_dashboard_resumen(db, usuario, hoy=date(2026, 10, 21))
    ultimos = resumen.get("ultimos_movimientos", [])
    descs = {m["descripcion"]: m for m in ultimos}

    assert "Reintegro: Silla" in descs
    assert "Silla" in descs

    m_reint = descs["Reintegro: Silla"]
    assert float(m_reint["monto"]) == 20000.0
    assert m_reint["sentido"] == "entra"

    m_silla = descs["Silla"]
    assert float(m_silla["monto"]) == 50000.0
    assert m_silla["sentido"] == "sale"


def test_convivencia_reintegro_y_parte(db: Session, usuario: Usuario, billetera_ars: Billetera, categorias: dict):
    """
    Convivencia: compra "Super" de 100.000 en débito el 05/10; ingreso de 30.000 el 06/10 pasado a parte de Super;
    reintegro de 20.000 recibido el 10/10: 2 espejos (uno "parte", uno "reintegro"),
    gasto de la categoría en octubre 50000.00, compra.reintegro.monto 20000.00;
    eliminar_reintegro: 1 espejo ("parte"), gasto 70000.00, compra.reintegro None.
    Con la parte de 30.000 puesta, un reintegro de 80.000: 400 "El reintegro no puede ser mayor que la compra.".
    """
    compra_data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Super",
        categoria_id=categorias["supermercado"].id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera_ars.id,
    )
    compra = transaccion_service.crear_transaccion(db, usuario.id, compra_data)

    ingreso_data = TransaccionCreate(
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 6),
        descripcion="Cobro amigo",
        categoria_id=categorias["otros_ingreso"].id,
        subcategoria_id=categorias["sub_cobros"].id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        billetera_id=billetera_ars.id,
    )
    ingreso = transaccion_service.crear_transaccion(db, usuario.id, ingreso_data)

    # Pasar ingreso a parte de Super
    que_es_plata_service.responder(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=ingreso.id,
        opcion="parte_de_gasto",
        gasto_id=compra.id,
    )

    # Reintegro de 20.000 recibido el 10/10
    reintegro_service.registrar_o_actualizar_reintegro(
        db=db,
        usuario_id=usuario.id,
        transaccion_id=compra.id,
        monto=Decimal("20000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 10),
    )

    # 2 espejos (uno "parte", uno "reintegro")
    espejos = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalars().all()
    assert len(espejos) == 2
    tipos = {e.tipo_espejo for e in espejos}
    assert tipos == {"parte", "reintegro"}

    # Gasto de la categoría en octubre: 50000.00
    stmt_gasto = select(Transaccion).where(
        condicion_gasto(usuario.id, desde=date(2026, 10, 1), hasta=date(2026, 10, 31), hoy=date(2026, 10, 21)),
        Transaccion.categoria_id == categorias["supermercado"].id,
    )
    total_gasto = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto == Decimal("50000.00")

    # compra.reintegro.monto 20000.00
    db.refresh(compra)
    assert compra.reintegro is not None
    assert compra.reintegro["monto"] == Decimal("20000.00")

    # eliminar_reintegro: 1 espejo ("parte"), gasto 70000.00, compra.reintegro None
    reintegro_service.eliminar_reintegro(db, usuario.id, compra.id)

    espejos_post = db.execute(
        select(Transaccion).where(Transaccion.reintegro_de_id == compra.id)
    ).scalars().all()
    assert len(espejos_post) == 1
    assert espejos_post[0].tipo_espejo == "parte"

    total_gasto_post = sum(t.monto for t in db.execute(stmt_gasto).scalars().all())
    assert total_gasto_post == Decimal("70000.00")

    db.refresh(compra)
    assert compra.reintegro is None

    # Con la parte de 30.000 puesta, un reintegro de 80.000 (disponible = 70.000): 400 "El reintegro no puede ser mayor que la compra."
    with pytest.raises(HTTPException) as exc:
        reintegro_service.registrar_o_actualizar_reintegro(
            db=db,
            usuario_id=usuario.id,
            transaccion_id=compra.id,
            monto=Decimal("80000.00"),
            recibido=True,
            fecha_recibido=date(2026, 10, 10),
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "El reintegro no puede ser mayor que la compra."
