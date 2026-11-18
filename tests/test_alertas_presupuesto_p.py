# -*- coding: utf-8 -*-
"""
Pruebas de alertas configurables por presupuesto (Bloque P: TP1 a TP9).
Fecha fija de referencia: 2026-11-17.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid
from uuid import uuid4

from fastapi import HTTPException
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
from app.core.reglas_texto_web import problemas_texto_web
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import MetodoPago, TipoTransaccion
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.schemas.presupuesto import PresupuestoCreate
from app.schemas.transaccion import TransaccionCreate
from app.services import (
    notificacion_service,
    presupuesto_service,
    transaccion_service,
)
from app.services.presupuesto_alertas import (
    alertas_por_defecto,
    grupo_alerta,
    umbral_a_avisar,
    validar_alertas,
)

HOY_FIJO = date(2026, 11, 17)
DETAIL_ERROR_ALERTAS = "Elegí de 1 a 3 avisos, de 10% a 100% y sin repetir."


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
        email="test_alertas_p@argentum.com",
        nombre="Usuario P",
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


def _fijar_hoy(monkeypatch, fecha: date):
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.presupuesto_service.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.services.notificacion_service.hoy_argentina", lambda: fecha)
    monkeypatch.setattr("app.routers.presupuestos.hoy_argentina", lambda: fecha)


def _crear_presupuesto_con_alertas(
    db: Session,
    usuario: Usuario,
    cat: Categoria,
    sub: Subcategoria,
    monto: Decimal,
    alertas: list[int],
) -> tuple[Presupuesto, PeriodoPresupuesto]:
    presu = Presupuesto(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Súper",
        monto=monto,
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime.now(timezone.utc),
        alertas_porcentajes=alertas,
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
        fecha_inicio=date(2026, 11, 1),
        fecha_fin=date(2026, 11, 30),
        monto_limite=monto,
    )
    db.add(periodo)
    db.commit()
    db.refresh(presu)
    db.refresh(periodo)
    return presu, periodo


def _registrar_gasto(
    db: Session,
    usuario: Usuario,
    cat: Categoria,
    sub: Subcategoria,
    billetera: Billetera,
    monto: Decimal,
    desc: str = "Gasto prueba",
):
    return transaccion_service.crear_transaccion(
        db,
        usuario.id,
        TransaccionCreate(
            monto=monto,
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
            fecha=HOY_FIJO,
            descripcion=desc,
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=billetera.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )


def _notifs_presupuesto(db: Session, usuario_id) -> list[Notificacion]:
    return (
        db.execute(
            select(Notificacion)
            .where(
                Notificacion.usuario_id == usuario_id,
                Notificacion.tipo.in_(
                    [
                        TipoNotificacion.PRESUPUESTO_LIMITE,
                        TipoNotificacion.PRESUPUESTO_AGOTADO,
                    ]
                ),
            )
            .order_by(Notificacion.created_at.asc())
        )
        .scalars()
        .all()
    )


def test_tp1_alertas_por_defecto():
    assert alertas_por_defecto(80, True) == [80, 100]
    assert alertas_por_defecto(70, True) == [70, 100]
    assert alertas_por_defecto(80, False) == [100]
    assert alertas_por_defecto(83, True) == [83, 100]
    assert alertas_por_defecto(82, True) == [82, 100]
    assert alertas_por_defecto(97, True) == [95, 100]
    assert alertas_por_defecto(48, True) == [50, 100]
    assert alertas_por_defecto(83, False) == [100]


@pytest.mark.parametrize(
    "entrada",
    [
        [],
        [9],
        [101],
        [105],
        [-5],
        [0],
        [80, 80],
        [83, 83],
        [10, 20, 30, 40],
        [True],
        [83.5],
        ["83"],
    ],
)
def test_tp2_validar_alertas_invalidas(entrada):
    with pytest.raises(HTTPException) as exc_info:
        validar_alertas(entrada)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == DETAIL_ERROR_ALERTAS


def test_tp2_validar_alertas_validas_ordenadas():
    assert validar_alertas([100]) == [100]
    assert validar_alertas([10]) == [10]
    assert validar_alertas([83]) == [83]
    assert validar_alertas([100, 53, 78]) == [53, 78, 100]


def test_tp3_umbral_a_avisar():
    assert umbral_a_avisar(Decimal("85"), [50, 80, 100], set()) == 80
    assert umbral_a_avisar(Decimal("101"), [50, 80, 100], {80}) == 100
    assert umbral_a_avisar(Decimal("80"), [50, 80, 100], set()) == 80
    assert umbral_a_avisar(Decimal("79.99"), [80, 100], set()) is None
    assert umbral_a_avisar(Decimal("79.99"), [50, 80, 100], {50}) is None
    assert umbral_a_avisar(Decimal("85"), [50, 80, 100], {80}) is None
    assert umbral_a_avisar(Decimal("60"), [50, 80, 100], {80}) is None
    assert umbral_a_avisar(Decimal("120"), [80], set()) == 80
    assert umbral_a_avisar(Decimal("82.99"), [83, 100], set()) is None
    assert umbral_a_avisar(Decimal("83"), [83, 100], set()) == 83


def test_tp4_salto_multiple_no_repite_ni_baja(
    db: Session,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    billetera_ars: Billetera,
    monkeypatch,
):
    _fijar_hoy(monkeypatch, HOY_FIJO)
    cat, sub = cat_sub_super
    presu, periodo = _crear_presupuesto_con_alertas(
        db, usuario, cat, sub, Decimal("10000.00"), [80, 100]
    )

    tx1 = _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("4000.00"), "Gasto 4k"
    )
    assert _notifs_presupuesto(db, usuario.id) == []

    # Cruza 80% y 100% de golpe (lleva de $4.000 a $11.000) -> solo umbral_100
    tx2 = _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("7000.00"), "Gasto 7k"
    )
    notifs = _notifs_presupuesto(db, usuario.id)
    assert len(notifs) == 1
    assert notifs[0].tipo == TipoNotificacion.PRESUPUESTO_AGOTADO
    assert notifs[0].grupo_agrupacion == grupo_alerta(presu.id, periodo.id, 100)

    # Otro egreso después no crea nada
    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("1000.00"), "Gasto 1k"
    )
    assert len(_notifs_presupuesto(db, usuario.id)) == 1

    # Borrar el gasto de $7.000 (baja a $5.000) y agregar $3.500 (85%) no crea 80
    _ = tx1
    transaccion_service.eliminar_transaccion(db, usuario.id, tx2.id)
    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("3500.00"), "Gasto 3.5k"
    )
    assert len(_notifs_presupuesto(db, usuario.id)) == 1


def test_tp5_tres_umbrales_progresivos(
    db: Session,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    billetera_ars: Billetera,
    monkeypatch,
):
    _fijar_hoy(monkeypatch, HOY_FIJO)
    cat, sub = cat_sub_super
    presu, periodo = _crear_presupuesto_con_alertas(
        db, usuario, cat, sub, Decimal("10000.00"), [50, 80, 100]
    )

    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("4000.00"), "Base 40%"
    )
    assert _notifs_presupuesto(db, usuario.id) == []

    # 40% -> 55%: avisa umbral 50 con porcentaje real 55%
    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("1500.00"), "Sube a 55%"
    )
    notifs_1 = _notifs_presupuesto(db, usuario.id)
    assert len(notifs_1) == 1
    assert notifs_1[0].tipo == TipoNotificacion.PRESUPUESTO_LIMITE
    assert notifs_1[0].grupo_agrupacion == grupo_alerta(presu.id, periodo.id, 50)
    assert notifs_1[0].mensaje.startswith("Ya usaste el 55% ")

    # 55% -> 85%: avisa umbral 80
    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("3000.00"), "Sube a 85%"
    )
    notifs_2 = _notifs_presupuesto(db, usuario.id)
    assert len(notifs_2) == 2
    assert notifs_2[1].tipo == TipoNotificacion.PRESUPUESTO_LIMITE
    assert notifs_2[1].grupo_agrupacion == grupo_alerta(presu.id, periodo.id, 80)
    assert notifs_2[1].mensaje.startswith("Ya usaste el 85% ")

    # 85% -> 100%: avisa umbral 100
    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("1500.00"), "Llega a 100%"
    )
    notifs_3 = _notifs_presupuesto(db, usuario.id)
    assert len(notifs_3) == 3
    assert notifs_3[2].tipo == TipoNotificacion.PRESUPUESTO_AGOTADO
    assert notifs_3[2].grupo_agrupacion == grupo_alerta(presu.id, periodo.id, 100)


def test_tp6_compatibilidad_grupos_viejos(
    db: Session,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    monkeypatch,
):
    _fijar_hoy(monkeypatch, HOY_FIJO)
    cat, sub = cat_sub_super
    presu, periodo = _crear_presupuesto_con_alertas(
        db, usuario, cat, sub, Decimal("10000.00"), [80, 100]
    )

    # Notificación vieja de límite en el período -> al 90% no vuelve a avisar
    grupo_viejo_lim = (
        f"presupuestos/{presu.id}/{periodo.id}/"
        f"{TipoNotificacion.PRESUPUESTO_LIMITE.value}"
    )
    db.add(
        Notificacion(
            id=uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.PRESUPUESTO_LIMITE,
            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
            mensaje="Ya usaste el 80% del presupuesto Súper: $8.000 de $10.000.",
            entidad_tipo="presupuesto",
            entidad_id=presu.id,
            grupo_agrupacion=grupo_viejo_lim,
        )
    )
    db.commit()

    presupuesto_service.verificar_alertas_presupuesto(
        db, presu, periodo, Decimal("9000.00")
    )
    assert len(_notifs_presupuesto(db, usuario.id)) == 1

    # Notificación vieja de agotado -> al 120% no vuelve a avisar
    grupo_viejo_agot = (
        f"presupuestos/{presu.id}/{periodo.id}/"
        f"{TipoNotificacion.PRESUPUESTO_AGOTADO.value}"
    )
    db.add(
        Notificacion(
            id=uuid4(),
            usuario_id=usuario.id,
            tipo=TipoNotificacion.PRESUPUESTO_AGOTADO,
            nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
            mensaje="Te pasaste del presupuesto Súper: gastaste $10.000 de $10.000.",
            entidad_tipo="presupuesto",
            entidad_id=presu.id,
            grupo_agrupacion=grupo_viejo_agot,
        )
    )
    db.commit()

    presupuesto_service.verificar_alertas_presupuesto(
        db, presu, periodo, Decimal("12000.00")
    )
    assert len(_notifs_presupuesto(db, usuario.id)) == 2


def test_tp7_perfil_inactivo_solo_afecta_nuevos(
    db: Session,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    monkeypatch,
):
    _fijar_hoy(monkeypatch, HOY_FIJO)
    cat, sub = cat_sub_super
    cfg = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=usuario.id,
        presupuesto_umbral_1=80,
        presupuesto_umbral_1_activo=False,
    )
    db.add(cfg)
    db.commit()

    # Presupuesto existente [80, 100] al 85% avisa igual aunque perfil esté False
    presu_existente, periodo = _crear_presupuesto_con_alertas(
        db, usuario, cat, sub, Decimal("10000.00"), [80, 100]
    )
    presupuesto_service.verificar_alertas_presupuesto(
        db, presu_existente, periodo, Decimal("8500.00")
    )
    notifs = _notifs_presupuesto(db, usuario.id)
    assert len(notifs) == 1
    assert notifs[0].grupo_agrupacion == grupo_alerta(
        presu_existente.id, periodo.id, 80
    )

    # Nuevo presupuesto creado sin alertas_porcentajes queda [100]
    nuevo = presupuesto_service.crear_presupuesto(
        db,
        usuario.id,
        PresupuestoCreate(
            nombre="Nuevo sin alertas",
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            categorias=[{"categoria_id": cat.id, "subcategoria_id": None}],
        ),
    )
    assert nuevo.alertas_porcentajes == [100]


def test_tp8_api_crud_y_defecto(
    db: Session,
    client: TestClient,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    monkeypatch,
):
    _fijar_hoy(monkeypatch, HOY_FIJO)
    cat, _ = cat_sub_super

    cfg = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=usuario.id,
        presupuesto_umbral_1=75,
        presupuesto_umbral_1_activo=True,
    )
    db.add(cfg)
    db.commit()

    resp_def = client.get("/presupuestos/alertas-por-defecto")
    assert resp_def.status_code == 200
    assert resp_def.json() == {"alertas_porcentajes": [75, 100]}

    resp_bad = client.post(
        "/presupuestos",
        json={
            "nombre": "Inválido",
            "monto": 10000,
            "moneda": "ARS",
            "periodo": "mensual",
            "renovacion": "automatica",
            "categorias": [{"categoria_id": str(cat.id), "subcategoria_id": None}],
            "alertas_porcentajes": [105],
        },
    )
    assert resp_bad.status_code == 422
    assert resp_bad.json()["error"]["message"] == DETAIL_ERROR_ALERTAS

    resp_ok = client.post(
        "/presupuestos",
        json={
            "nombre": "Ordenado",
            "monto": 10000,
            "moneda": "ARS",
            "periodo": "mensual",
            "renovacion": "automatica",
            "categorias": [{"categoria_id": str(cat.id), "subcategoria_id": None}],
            "alertas_porcentajes": [100, 50],
        },
    )
    assert resp_ok.status_code == 200
    data = resp_ok.json()
    assert data["alertas_porcentajes"] == [50, 100]
    presu_id = data["id"]

    # Editar sin el campo no cambia alertas_porcentajes
    resp_put_sin = client.put(
        f"/presupuestos/{presu_id}",
        json={"nombre": "Ordenado 2"},
    )
    assert resp_put_sin.status_code == 200
    assert resp_put_sin.json()["alertas_porcentajes"] == [50, 100]

    # Editar con [67] lo cambia (admite enteros de 10 a 100)
    resp_put_con = client.put(
        f"/presupuestos/{presu_id}",
        json={"alertas_porcentajes": [67]},
    )
    assert resp_put_con.status_code == 200
    assert resp_put_con.json()["alertas_porcentajes"] == [67]

    # Editar con duplicados o fuera de rango devuelve 422
    resp_put_dup = client.put(
        f"/presupuestos/{presu_id}",
        json={"alertas_porcentajes": [83, 83]},
    )
    assert resp_put_dup.status_code == 422
    assert resp_put_dup.json()["error"]["message"] == DETAIL_ERROR_ALERTAS

    resp_put_bajo = client.put(
        f"/presupuestos/{presu_id}",
        json={"alertas_porcentajes": [9]},
    )
    assert resp_put_bajo.status_code == 422
    assert resp_put_bajo.json()["error"]["message"] == DETAIL_ERROR_ALERTAS


def test_tp9_textos_cumplen_reglas_web():
    textos = [
        DETAIL_ERROR_ALERTAS,
        "Te llega un aviso al 80%.",
        "Te llega un aviso al 83% y al 100%.",
        "Te llega un aviso al 50%, al 80% y al 100%.",
    ]
    for texto in textos:
        assert problemas_texto_web(texto) == []


def test_tp10_actualizar_configuracion_respeta_rango_umbral(
    db: Session,
    usuario: Usuario,
):
    casos = [
        (83, 83),
        (82, 82),
        (99, 95),
        (40, 50),
        (75, 75),
    ]
    for entrada, esperado in casos:
        cfg = notificacion_service.actualizar_configuracion(
            db,
            usuario.id,
            {"presupuesto_umbral_1": entrada},
        )
        assert cfg.presupuesto_umbral_1 == esperado


def test_tp11_perfil_exacto_al_leer_y_crear(
    db: Session,
    client: TestClient,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    monkeypatch,
):
    _fijar_hoy(monkeypatch, HOY_FIJO)
    cat, _ = cat_sub_super
    cfg = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=usuario.id,
        presupuesto_umbral_1=83,
        presupuesto_umbral_1_activo=True,
    )
    db.add(cfg)
    db.commit()

    resp_def = client.get("/presupuestos/alertas-por-defecto")
    assert resp_def.status_code == 200
    assert resp_def.json() == {"alertas_porcentajes": [83, 100]}

    resp_post = client.post(
        "/presupuestos",
        json={
            "nombre": "Perfil 83",
            "monto": 10000,
            "moneda": "ARS",
            "periodo": "mensual",
            "renovacion": "automatica",
            "categorias": [
                {"categoria_id": str(cat.id), "subcategoria_id": None}
            ],
        },
    )
    assert resp_post.status_code == 200
    assert resp_post.json()["alertas_porcentajes"] == [83, 100]


def test_tp12_aviso_con_porcentaje_libre(
    db: Session,
    usuario: Usuario,
    cat_sub_super: tuple[Categoria, Subcategoria],
    billetera_ars: Billetera,
    monkeypatch,
):
    _fijar_hoy(monkeypatch, HOY_FIJO)
    cat, sub = cat_sub_super
    presu, periodo = _crear_presupuesto_con_alertas(
        db, usuario, cat, sub, Decimal("10000.00"), [83, 100]
    )

    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("8200.00"), "Gasto 82%"
    )
    assert _notifs_presupuesto(db, usuario.id) == []

    _registrar_gasto(
        db, usuario, cat, sub, billetera_ars, Decimal("100.00"), "Llega 83%"
    )
    notifs = _notifs_presupuesto(db, usuario.id)
    assert len(notifs) == 1
    assert notifs[0].tipo == TipoNotificacion.PRESUPUESTO_LIMITE
    assert notifs[0].grupo_agrupacion == grupo_alerta(presu.id, periodo.id, 83)
    assert notifs[0].mensaje.startswith("Ya usaste el 83% ")
