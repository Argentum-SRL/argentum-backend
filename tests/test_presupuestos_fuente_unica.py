"""
tests/test_presupuestos_fuente_unica.py — Pruebas de fuente única del usado de presupuestos,
camino único de aviso, gastos futuros, reintegros en períodos distintos (D-P8) y controles AST (TE.1, TE.2, TE.3, TE.13, TE.E).
"""
from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import inspect
from pathlib import Path
import uuid
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.services import (
    ai_service,
    contexto_financiero_service,
    presupuesto_service,
    reintegro_service,
    transaccion_service,
)


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
        email="test_fuente_unica@argentum.com",
        nombre="Usuario Prueba",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(name="cat_sub_super")
def cat_sub_super_fixture(db: Session):
    cat = Categoria(
        id=uuid4(),
        nombre="Alimentación",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(cat)
    db.flush()
    sub = Subcategoria(
        id=uuid4(),
        categoria_id=cat.id,
        nombre="Supermercado",
    )
    db.add(sub)
    db.commit()
    db.refresh(cat)
    db.refresh(sub)
    return cat, sub


@pytest.fixture(name="billetera_ars")
def billetera_ars_fixture(db: Session, usuario: Usuario):
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        fecha_saldo=date(2026, 1, 1),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(billetera)
    db.commit()
    db.refresh(billetera)
    return billetera


@pytest.fixture(name="tarjeta_credito")
def tarjeta_credito_fixture(db: Session, usuario: Usuario, billetera_ars: Billetera):
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Visa Galicia",
        limite_credito=Decimal("2000000.00"),
        dia_cierre=22,
        dia_vencimiento=4,
        billetera_id=billetera_ars.id,
        red=RedTarjeta.VISA,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)
    db.commit()
    db.refresh(tarjeta)
    return tarjeta


def _fijar_hoy(monkeypatch, fecha: date):
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.routers.presupuestos.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.ai_service.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.contexto_financiero_service.hoy_argentina", lambda: fecha)


# ==============================================================================
# TE.1: Compra con tarjeta de crédito en 1 pago, uso_actual, API, WhatsApp, IA y alertas 80%/100%
# ==============================================================================
def test_te1_compra_credito_1_pago_fuente_unica_y_alertas(
    db: Session,
    client: TestClient,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    billetera_ars: Billetera,
    tarjeta_credito: TarjetaCredito,
    monkeypatch,
):
    hoy = date(2026, 10, 15)
    _fijar_hoy(monkeypatch, hoy)
    cat, sub = cat_sub_super

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Súper",
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(presu)
    db.flush()
    db.add(
        PresupuestoCategoria(
            presupuesto_id=presu.id,
            categoria_id=cat.id,
            subcategoria_id=sub.id,
        )
    )
    periodo = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=hoy - timedelta(days=10),
        fecha_fin=hoy + timedelta(days=20),
        monto_limite=Decimal("120000.00"),
    )
    db.add(periodo)
    db.commit()
    db.refresh(presu)

    # 1. Compra con tarjeta de crédito en 1 pago de $100.000 hoy en Supermercado
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra super 1 pago",
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=billetera_ars.id,
            tarjeta_id=tarjeta_credito.id,
            metodo_pago=MetodoPago.CREDITO,
        ),
    )

    per_act, uso = presupuesto_service.uso_actual(db, presu, hoy=hoy)
    assert per_act is not None and per_act.id == periodo.id
    assert uso is not None
    assert uso.mostrado == Decimal("100000.00")

    # GET /presupuestos/{id}
    resp = client.get(f"/presupuestos/{presu.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(str(body["periodo_actual"]["monto_usado"])) == Decimal("100000.00")
    assert round(float(body["periodo_actual"]["porcentaje_usado"]), 2) == 83.33

    # _resumen_presupuestos_activos_sync
    res_wpp = contexto_financiero_service._resumen_presupuestos_activos_sync(db, usuario.id)
    assert len(res_wpp) == 1
    assert res_wpp[0]["usado"] == 100000.0

    # _construir_contexto_financiero_uncached
    ctx_ia = ai_service._construir_contexto_financiero_uncached(usuario, db)
    pres_ia = ctx_ia["presupuestos_activos"]
    assert len(pres_ia) == 1
    assert pres_ia[0]["monto_usado"] == 100000.0
    assert pres_ia[0]["monto_disponible"] == 20000.0

    # Exactamente 1 notificación PRESUPUESTO_LIMITE
    notifs_lim = (
        db.execute(
            select(Notificacion).where(
                Notificacion.usuario_id == usuario.id,
                Notificacion.tipo == TipoNotificacion.PRESUPUESTO_LIMITE,
            )
        )
        .scalars()
        .all()
    )
    assert len(notifs_lim) == 1
    assert notifs_lim[0].grupo_agrupacion == f"presupuestos/{presu.id}/{periodo.id}/{TipoNotificacion.PRESUPUESTO_LIMITE.value}"
    assert (
        notifs_lim[0].mensaje
        == "Llevás gastado el 83% de tu presupuesto 'Súper' ($100.000 de $120.000)."
    )

    # 2. Otra compra de $1.000: sigue 1 PRESUPUESTO_LIMITE
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("1000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra 1000",
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    notifs_lim_2 = (
        db.execute(
            select(Notificacion).where(
                Notificacion.usuario_id == usuario.id,
                Notificacion.tipo == TipoNotificacion.PRESUPUESTO_LIMITE,
            )
        )
        .scalars()
        .all()
    )
    assert len(notifs_lim_2) == 1

    # 3. Otra de $30.000 (total $131.000): 1 PRESUPUESTO_AGOTADO
    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy,
            descripcion="Compra 30000",
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )
    notifs_agot = (
        db.execute(
            select(Notificacion).where(
                Notificacion.usuario_id == usuario.id,
                Notificacion.tipo == TipoNotificacion.PRESUPUESTO_AGOTADO,
            )
        )
        .scalars()
        .all()
    )
    assert len(notifs_agot) == 1
    assert (
        notifs_agot[0].mensaje
        == "Gastaste $131.000 de $120.000 en Súper. Superaste el límite que te pusiste."
    )


# ==============================================================================
# TE.2: Egreso con fecha hoy+5 no cuenta en hoy ni avisa; cuenta en hoy+5
# ==============================================================================
def test_te2_egreso_futuro_no_cuenta_en_hoy(
    db: Session,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    billetera_ars: Billetera,
    monkeypatch,
):
    hoy = date(2026, 10, 15)
    _fijar_hoy(monkeypatch, hoy)
    cat, sub = cat_sub_super

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Súper",
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(presu)
    db.flush()
    db.add(
        PresupuestoCategoria(
            presupuesto_id=presu.id,
            categoria_id=cat.id,
            subcategoria_id=sub.id,
        )
    )
    periodo = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=hoy - timedelta(days=10),
        fecha_fin=hoy + timedelta(days=20),
        monto_limite=Decimal("120000.00"),
    )
    db.add(periodo)
    db.commit()
    db.refresh(presu)

    transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("10000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy + timedelta(days=5),
            descripcion="Gasto futuro",
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    uso_hoy = presupuesto_service.uso_periodo(db, presu, periodo, hoy=hoy, usuario=usuario)
    assert uso_hoy.mostrado == Decimal("0.00")

    notifs = (
        db.execute(
            select(Notificacion).where(
                Notificacion.usuario_id == usuario.id,
                Notificacion.tipo.in_(
                    [TipoNotificacion.PRESUPUESTO_LIMITE, TipoNotificacion.PRESUPUESTO_AGOTADO]
                ),
            )
        )
        .scalars()
        .all()
    )
    assert len(notifs) == 0

    uso_futuro = presupuesto_service.uso_periodo(
        db, presu, periodo, hoy=hoy + timedelta(days=5), usuario=usuario
    )
    assert uso_futuro.mostrado == Decimal("10000.00")


# ==============================================================================
# TE.3: P8 — Reintegro en período siguiente con usado calculado negativo y mostrado 0
# ==============================================================================
def test_te3_p8_reintegro_periodo_siguiente_y_api_historial(
    db: Session,
    client: TestClient,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    billetera_ars: Billetera,
    monkeypatch,
):
    hoy = date(2026, 10, 15)
    _fijar_hoy(monkeypatch, hoy)
    cat, sub = cat_sub_super

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Súper",
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(presu)
    db.flush()
    db.add(
        PresupuestoCategoria(
            presupuesto_id=presu.id,
            categoria_id=cat.id,
            subcategoria_id=sub.id,
        )
    )
    per_sep = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 9, 1),
        fecha_fin=date(2026, 9, 30),
        monto_limite=Decimal("120000.00"),
    )
    per_oct = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=date(2026, 10, 1),
        fecha_fin=date(2026, 10, 31),
        monto_limite=Decimal("120000.00"),
    )
    db.add_all([per_sep, per_oct])
    db.commit()
    db.refresh(presu)

    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=date(2026, 9, 28),
            descripcion="Compra Septiembre",
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=date(2026, 10, 2),
    )

    uso_sep = presupuesto_service.uso_periodo(db, presu, per_sep, hoy=hoy, usuario=usuario)
    uso_oct = presupuesto_service.uso_periodo(db, presu, per_oct, hoy=hoy, usuario=usuario)

    assert uso_sep.calculado == Decimal("100000.00")
    assert uso_sep.mostrado == Decimal("100000.00")
    assert uso_oct.calculado == Decimal("-25000.00")
    assert uso_oct.mostrado == Decimal("0.00")
    assert uso_oct.supera_limite is False
    assert uso_oct.porcentaje == 0.0

    resp = client.get(f"/presupuestos/{presu.id}/historial")
    assert resp.status_code == 200
    hist = resp.json()
    assert len(hist) == 2

    campos_esperados = {
        "id",
        "presupuesto_id",
        "fecha_inicio",
        "fecha_fin",
        "monto_limite",
        "monto_usado",
        "superado",
        "porcentaje_usado",
        "dias_restantes",
        "monto_propio",
        "monto_convertido",
        "monto_sin_cotizacion",
        "moneda_sin_cotizacion",
    }
    for item in hist:
        assert set(item.keys()) == campos_esperados

    por_inicio = {item["fecha_inicio"]: item for item in hist}
    oct_item = por_inicio["2026-10-01"]
    sep_item = por_inicio["2026-09-01"]

    assert Decimal(str(oct_item["monto_usado"])) == Decimal("0")
    assert oct_item["superado"] is False
    assert oct_item["porcentaje_usado"] == 0
    assert Decimal(str(sep_item["monto_usado"])) == Decimal("100000.00")


# ==============================================================================
# TE.13: Débito + reintegro recibido + cuota hija de grupo de 3 cuotas = 85.000 en las 4 vistas
# ==============================================================================
def test_te13_consistencia_cuatro_lectores(
    db: Session,
    client: TestClient,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    billetera_ars: Billetera,
    tarjeta_credito: TarjetaCredito,
    monkeypatch,
):
    hoy = date(2026, 10, 15)
    _fijar_hoy(monkeypatch, hoy)
    cat, sub = cat_sub_super

    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Súper",
        monto=Decimal("200000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(presu)
    db.flush()
    db.add(
        PresupuestoCategoria(
            presupuesto_id=presu.id,
            categoria_id=cat.id,
            subcategoria_id=sub.id,
        )
    )
    periodo = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=presu.id,
        fecha_inicio=hoy - timedelta(days=10),
        fecha_fin=hoy + timedelta(days=20),
        monto_limite=Decimal("200000.00"),
    )
    db.add(periodo)
    db.commit()
    db.refresh(presu)

    # 1. Compra con débito de $100.000 hoy-3
    compra = transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=Decimal("100000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=hoy - timedelta(days=3),
            descripcion="Compra débito",
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=billetera_ars.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    # 2. Reintegro recibido de $25.000 hoy-2
    reintegro_service.registrar_o_actualizar_reintegro(
        db,
        usuario.id,
        compra.id,
        monto=Decimal("25000.00"),
        recibido=True,
        fecha_recibido=hoy - timedelta(days=2),
    )

    # 3. Una cuota hija de $10.000 con fecha hoy-1 de un grupo de 3 cuotas con crédito en Supermercado
    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta_credito.id,
        categoria_id=cat.id,
        subcategoria_id=sub.id,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy - timedelta(days=35),
        descripcion="Compra 3 cuotas",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=True,
        es_cuota_hija=False,
    )
    db.add(tx_padre)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        transaccion_padre_id=tx_padre.id,
        tarjeta_id=tarjeta_credito.id,
        descripcion="Compra 3 cuotas",
        monto_total=Decimal("30000.00"),
        total_financiado=Decimal("30000.00"),
        moneda=Moneda.ARS,
        cantidad_cuotas=3,
        tiene_interes=False,
        primer_vencimiento=hoy - timedelta(days=1),
        estado=EstadoGrupoCuotas.ACTIVO,
    )
    db.add(grupo)
    db.flush()
    tx_padre.grupo_cuotas_id = grupo.id

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_ars.id,
        tarjeta_id=tarjeta_credito.id,
        categoria_id=cat.id,
        subcategoria_id=sub.id,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        tipo=TipoTransaccion.EGRESO,
        fecha=hoy - timedelta(days=1),
        descripcion="Compra 3 cuotas (Cuota 2/3)",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        grupo_cuotas_id=grupo.id,
    )
    db.add(tx_hija)
    db.flush()
    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=2,
        monto_proyectado=Decimal("10000.00"),
        fecha_vencimiento=hoy - timedelta(days=1),
        pagada=False,
    )
    db.add(cuota)
    db.commit()

    # 1. uso_actual
    _per, uso = presupuesto_service.uso_actual(db, presu, hoy=hoy)
    assert uso is not None
    assert uso.mostrado == Decimal("85000.00")

    # 2. API
    resp = client.get(f"/presupuestos/{presu.id}")
    assert resp.status_code == 200
    assert Decimal(str(resp.json()["periodo_actual"]["monto_usado"])) == Decimal("85000.00")

    # 3. _resumen_presupuestos_activos_sync
    res_wpp = contexto_financiero_service._resumen_presupuestos_activos_sync(db, usuario.id)
    assert len(res_wpp) == 1
    assert res_wpp[0]["usado"] == 85000.0

    # 4. Contexto de la IA
    ctx_ia = ai_service._construir_contexto_financiero_uncached(usuario, db)
    assert ctx_ia["presupuestos_activos"][0]["monto_usado"] == 85000.0


# ==============================================================================
# TE.E: Pruebas estáticas por AST (E.1 a E.5)
# ==============================================================================
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_tee_1_ningun_atributo_monto_usado_superado_ni_monto_usado_actual():
    """E.1: En app\\ y scripts\\ ningún atributo .monto_usado, .superado ni .monto_usado_actual."""
    prohibidos = {"monto_usado", "superado", "monto_usado_actual"}
    violaciones = []
    for carpeta in (REPO_ROOT / "app", REPO_ROOT / "scripts"):
        for py_file in sorted(carpeta.rglob("*.py")):
            rel = py_file.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in prohibidos:
                    violaciones.append(f"{rel}:{node.lineno} (.{node.attr})")
    assert violaciones == [], f"Atributos prohibidos encontrados: {violaciones}"


def test_tee_2_calcular_gasto_en_periodo_solo_en_presupuesto_service():
    """E.2: En app\\ y scripts\\ el nombre calcular_gasto_en_periodo aparece solo en app/services/presupuesto_service.py."""
    apariciones_fuera = []
    for carpeta in (REPO_ROOT / "app", REPO_ROOT / "scripts"):
        for py_file in sorted(carpeta.rglob("*.py")):
            rel = py_file.relative_to(REPO_ROOT).as_posix()
            if rel == "app/services/presupuesto_service.py":
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id == "calcular_gasto_en_periodo":
                    apariciones_fuera.append(f"{rel}:{node.lineno}")
                elif isinstance(node, ast.Attribute) and node.attr == "calcular_gasto_en_periodo":
                    apariciones_fuera.append(f"{rel}:{node.lineno}")
                elif isinstance(node, ast.alias) and node.name == "calcular_gasto_en_periodo":
                    apariciones_fuera.append(f"{rel}:{node.lineno}")
    assert apariciones_fuera == [], f"calcular_gasto_en_periodo fuera de presupuesto_service.py: {apariciones_fuera}"


def test_tee_3_asignaciones_monto_actual_solo_en_recalcular_acumulado():
    """E.3: En app\\ las únicas asignaciones a .monto_actual (=, += o -=) están dentro de meta_service.recalcular_acumulado."""
    violaciones = []
    asignaciones_validas = 0
    for py_file in sorted((REPO_ROOT / "app").rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=rel)

        func_por_nodo = {}

        def _visitar(nodo, func_actual="<modulo>"):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_actual = nodo.name
            func_por_nodo[id(nodo)] = func_actual
            for hijo in ast.iter_child_nodes(nodo):
                _visitar(hijo, func_actual)

        _visitar(tree)

        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and node.target is not None:
                targets.append(node.target)

            for tgt in targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "monto_actual":
                    fn = func_por_nodo.get(id(node), "<modulo>")
                    if rel == "app/services/meta_service.py" and fn == "recalcular_acumulado":
                        asignaciones_validas += 1
                    else:
                        violaciones.append(f"{rel}:{node.lineno} ({fn})")

    assert asignaciones_validas >= 1
    assert violaciones == [], f"Asignaciones a .monto_actual fuera de meta_service.recalcular_acumulado: {violaciones}"


def test_tee_4_scheduler_no_nombra_presupuesto_limite_ni_agotado():
    """E.4: notificacion_scheduler_service.py no nombra PRESUPUESTO_LIMITE ni PRESUPUESTO_AGOTADO."""
    sched_path = REPO_ROOT / "app" / "services" / "notificacion_scheduler_service.py"
    code = sched_path.read_text(encoding="utf-8")
    assert "PRESUPUESTO_LIMITE" not in code
    assert "PRESUPUESTO_AGOTADO" not in code


def test_tee_5_firma_registrar_impacto_presupuesto():
    """E.5: registrar_impacto_presupuesto tiene la firma (db, transaccion, revertir=False, commit=True)."""
    sig = inspect.signature(presupuesto_service.registrar_impacto_presupuesto)
    params = list(sig.parameters.values())
    assert [p.name for p in params] == ["db", "transaccion", "revertir", "commit"]
    assert params[0].default is inspect.Parameter.empty
    assert params[1].default is inspect.Parameter.empty
    assert params[2].default is False
    assert params[3].default is True

