# -*- coding: utf-8 -*-
"""
tests/test_permitirse_personas_t1.py — Pruebas de punta a punta con personas (PA a PH).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import sqlite3
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.permitirse_dolares import usa_dolares_seguido
from app.services.permitirse_service import calcular_puede_permitirse
from app.utils.fecha import TZ_ARGENTINA

HOY_FIJO = date(2026, 11, 17)
FECHA_SALDO_FIJA = date(2026, 3, 1)
CICLOS_CERRADOS = [
    date(2026, 3, 1),
    date(2026, 4, 1),
    date(2026, 5, 1),
    date(2026, 6, 1),
    date(2026, 7, 1),
    date(2026, 8, 1),
    date(2026, 9, 1),
    date(2026, 10, 1),
]


@pytest.fixture(autouse=True)
def _compat_y_fecha(monkeypatch):
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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    for mod in (
        "app.utils.fecha",
        "app.services.permitirse_service",
        "app.services.fin_de_mes_service",
        "app.services.frescura_service",
        "app.services.compromisos_service",
        "app.services.ingreso_habitual_service",
        "app.services.definiciones_service",
        "app.services.pagos_proximos_service",
        "app.services.dolar_service",
        "app.services.tools_service",
    ):
        monkeypatch.setattr(f"{mod}.hoy_argentina", lambda: HOY_FIJO, raising=False)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db")
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
    _seed_basico(session)
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _seed_basico(db: Session) -> None:
    for fecha_cot in (date(2026, 1, 1), HOY_FIJO):
        db.add(CotizacionDolar(
            fecha=fecha_cot,
            tipo="oficial",
            compra=Decimal("1400.00"),
            venta=Decimal("1450.00"),
            promedio=Decimal("1425.00"),
            fecha_registro=datetime(2026, 11, 17, 10, 0, tzinfo=TZ_ARGENTINA),
        ))

    cat_trabajo = Categoria(
        id=uuid4(),
        nombre="Trabajo",
        tipo=TipoCategoria.INGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_alim = Categoria(
        id=uuid4(),
        nombre="Alimentación",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_gast = Categoria(
        id=uuid4(),
        nombre="Gastronomía",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([cat_trabajo, cat_alim, cat_gast])
    db.flush()

    db.add_all([
        Subcategoria(
            id=uuid4(),
            categoria_id=cat_trabajo.id,
            nombre="Sueldo",
            estado=EstadoSubcategoria.ACTIVA,
        ),
        Subcategoria(
            id=uuid4(),
            categoria_id=cat_alim.id,
            nombre="Supermercado",
            estado=EstadoSubcategoria.ACTIVA,
        ),
        Subcategoria(
            id=uuid4(),
            categoria_id=cat_alim.id,
            nombre="Kiosco",
            estado=EstadoSubcategoria.ACTIVA,
        ),
        Subcategoria(
            id=uuid4(),
            categoria_id=cat_gast.id,
            nombre="Delivery",
            estado=EstadoSubcategoria.ACTIVA,
        ),
    ])
    db.commit()


def _cats(db: Session) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    res: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    for nombre in ("Sueldo", "Supermercado", "Kiosco", "Delivery"):
        sub = db.query(Subcategoria).filter(Subcategoria.nombre == nombre).first()
        res[nombre] = (sub.categoria_id, sub.id)
    return res


def _crear_usuario(
    db: Session,
    email: str,
    moneda: Moneda = Moneda.ARS,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=moneda,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2026, 2, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    u.is_admin = False
    db.add(u)
    db.flush()
    return u


def _crear_billetera(
    db: Session,
    usuario_id: uuid.UUID,
    nombre: str,
    moneda: Moneda,
    saldo: Decimal,
    es_inversion: bool = False,
) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre=nombre,
        moneda=moneda,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=es_inversion,
        saldo_actual=saldo,
        saldo_inicial=saldo,
        fecha_saldo=FECHA_SALDO_FIJA,
        tiene_punto_partida=True,
    )
    db.add(b)
    db.flush()
    return b


def _agregar_tx(
    db: Session,
    usuario_id: uuid.UUID,
    billetera_id: uuid.UUID,
    cat_sub: tuple[uuid.UUID, uuid.UUID],
    tipo: TipoTransaccion,
    moneda: Moneda,
    monto: Decimal,
    fecha: date,
    metodo: MetodoPago = MetodoPago.DEBITO,
    tarjeta_id: uuid.UUID | None = None,
    reintegro_de_id: uuid.UUID | None = None,
    tipo_espejo: str | None = None,
) -> Transaccion:
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        categoria_id=cat_sub[0],
        subcategoria_id=cat_sub[1],
        tipo=tipo,
        metodo_pago=metodo,
        tarjeta_id=tarjeta_id,
        moneda=moneda,
        monto=monto,
        fecha=fecha,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
        reintegro_de_id=reintegro_de_id,
        tipo_espejo=tipo_espejo,
        fecha_creacion=datetime(fecha.year, fecha.month, fecha.day, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(tx)
    return tx


def _armar_pa_juan(db: Session) -> Usuario:
    c = _cats(db)
    u = _crear_usuario(db, "pa_juan@argentum.com", Moneda.ARS)
    galicia = _crear_billetera(db, u.id, "Galicia", Moneda.ARS, Decimal("6000000"))
    for ini in CICLOS_CERRADOS:
        _agregar_tx(
            db, u.id, galicia.id, c["Sueldo"],
            TipoTransaccion.INGRESO, Moneda.ARS, Decimal("1200000"), ini,
        )
        _agregar_tx(
            db, u.id, galicia.id, c["Supermercado"],
            TipoTransaccion.EGRESO, Moneda.ARS, Decimal("900000"), ini.replace(day=10),
        )
        _agregar_tx(
            db, u.id, galicia.id, c["Delivery"],
            TipoTransaccion.EGRESO, Moneda.ARS, Decimal("300000"), ini.replace(day=15),
        )
    _agregar_tx(
        db, u.id, galicia.id, c["Sueldo"],
        TipoTransaccion.INGRESO, Moneda.ARS, Decimal("1200000"), date(2026, 11, 1),
    )
    _agregar_tx(
        db, u.id, galicia.id, c["Supermercado"],
        TipoTransaccion.EGRESO, Moneda.ARS, Decimal("50000"), date(2026, 11, 15),
    )
    db.commit()
    return u


def test_pa_juan(db: Session):
    u = _armar_pa_juan(db)

    r_contado = calcular_puede_permitirse(u.id, 500000, "contado", db=db, hoy=HOY_FIJO)
    assert r_contado["resultado"] == "bien"

    r_cuotas = calcular_puede_permitirse(
        u.id, 600000, "cuotas", cantidad_cuotas=6, tiene_interes=False, db=db, hoy=HOY_FIJO
    )
    assert r_cuotas["resultado"] == "justo"
    assert (
        r_cuotas["linea_principal"]
        == "De cada cuota, unos $100.000 salen de la plata que ya tenés en tus cuentas."
    )
    assert r_cuotas["linea_cuentas"] == "Te alcanza para las 6 cuotas."
    assert r_cuotas["linea_liberacion"] is None

    r_mensual = calcular_puede_permitirse(u.id, 50000, "mensual", db=db, hoy=HOY_FIJO)
    assert r_mensual["resultado"] == "justo"
    assert r_mensual["linea_principal"] == "Con la plata de tus cuentas lo bancás más de 2 años."


def test_pb_martina(db: Session):
    c = _cats(db)
    u = _crear_usuario(db, "pb_martina@argentum.com", Moneda.ARS)
    cta_usd = _crear_billetera(db, u.id, "Cuenta dólares", Moneda.USD, Decimal("8000"))
    galicia = _crear_billetera(db, u.id, "Galicia", Moneda.ARS, Decimal("300000"))
    for ini in CICLOS_CERRADOS:
        _agregar_tx(
            db, u.id, cta_usd.id, c["Sueldo"],
            TipoTransaccion.INGRESO, Moneda.USD, Decimal("2500"), ini,
        )
        _agregar_tx(
            db, u.id, galicia.id, c["Supermercado"],
            TipoTransaccion.EGRESO, Moneda.ARS, Decimal("1500000"), ini.replace(day=12),
        )
    _agregar_tx(
        db, u.id, cta_usd.id, c["Sueldo"],
        TipoTransaccion.INGRESO, Moneda.USD, Decimal("2500"), date(2026, 11, 1),
    )
    _agregar_tx(
        db, u.id, galicia.id, c["Supermercado"],
        TipoTransaccion.EGRESO, Moneda.ARS, Decimal("100000"), date(2026, 11, 15),
    )
    db.commit()

    assert usa_dolares_seguido(db, u, HOY_FIJO) is True

    r100 = calcular_puede_permitirse(u.id, 100000, "contado", db=db, hoy=HOY_FIJO)
    assert r100["resultado"] == "bien"
    assert "no conozco bien tus meses" not in r100["linea_principal"]

    r500 = calcular_puede_permitirse(u.id, 500000, "contado", db=db, hoy=HOY_FIJO)
    assert r500["resultado"] == "bien"

    rm = calcular_puede_permitirse(u.id, 50000, "mensual", db=db, hoy=HOY_FIJO)
    assert rm["motivo"] != "sin_ingreso"
    assert "Cuando cargues" not in rm["linea_principal"]


def test_pc_ana(db: Session):
    c = _cats(db)
    u = _crear_usuario(db, "pc_ana@argentum.com", Moneda.ARS)
    galicia = _crear_billetera(db, u.id, "Galicia", Moneda.ARS, Decimal("200000"))
    _crear_billetera(db, u.id, "Efectivo Dólares", Moneda.USD, Decimal("5000"))
    for ini in CICLOS_CERRADOS:
        _agregar_tx(
            db, u.id, galicia.id, c["Sueldo"],
            TipoTransaccion.INGRESO, Moneda.ARS, Decimal("1000000"), ini,
        )
        _agregar_tx(
            db, u.id, galicia.id, c["Supermercado"],
            TipoTransaccion.EGRESO, Moneda.ARS, Decimal("900000"), ini.replace(day=12),
        )
    _agregar_tx(
        db, u.id, galicia.id, c["Sueldo"],
        TipoTransaccion.INGRESO, Moneda.ARS, Decimal("1000000"), date(2026, 11, 1),
    )
    _agregar_tx(
        db, u.id, galicia.id, c["Supermercado"],
        TipoTransaccion.EGRESO, Moneda.ARS, Decimal("50000"), date(2026, 11, 15),
    )
    db.commit()

    assert usa_dolares_seguido(db, u, HOY_FIJO) is False

    r = calcular_puede_permitirse(u.id, 1000000, "contado", db=db, hoy=HOY_FIJO)
    assert r["resultado"] == "ahorros"
    assert r["linea_ahorros"] == "Tus ahorros suman $7.000.000."


def test_pd_lucas(db: Session):
    c = _cats(db)
    u = _crear_usuario(db, "pd_lucas@argentum.com", Moneda.ARS)
    galicia = _crear_billetera(db, u.id, "Galicia", Moneda.ARS, Decimal("300000"))
    sueldos = [
        Decimal("920000"), Decimal("1080000"), Decimal("960000"), Decimal("1040000"),
        Decimal("920000"), Decimal("1080000"), Decimal("960000"), Decimal("1040000"),
    ]
    sobrantes = [
        Decimal("40000"), Decimal("60000"), Decimal("45000"), Decimal("55000"),
        Decimal("42000"), Decimal("58000"), Decimal("48000"), Decimal("52000"),
    ]
    for ini, sueldo, sobra in zip(CICLOS_CERRADOS, sueldos, sobrantes):
        _agregar_tx(
            db, u.id, galicia.id, c["Sueldo"],
            TipoTransaccion.INGRESO, Moneda.ARS, sueldo, ini,
        )
        _agregar_tx(
            db, u.id, galicia.id, c["Supermercado"],
            TipoTransaccion.EGRESO, Moneda.ARS, sueldo - sobra, ini.replace(day=12),
        )
    _agregar_tx(
        db, u.id, galicia.id, c["Sueldo"],
        TipoTransaccion.INGRESO, Moneda.ARS, Decimal("1000000"), date(2026, 11, 1),
    )
    _agregar_tx(
        db, u.id, galicia.id, c["Supermercado"],
        TipoTransaccion.EGRESO, Moneda.ARS, Decimal("50000"), date(2026, 11, 15),
    )
    db.commit()

    rm = calcular_puede_permitirse(u.id, 20000, "mensual", db=db, hoy=HOY_FIJO)
    assert rm["resultado"] in ("bien", "justo")


def test_pe_sin_liberacion(db: Session):
    u = _armar_pa_juan(db)
    r = calcular_puede_permitirse(
        u.id, 6000000, "cuotas", cantidad_cuotas=6, tiene_interes=False, db=db, hoy=HOY_FIJO
    )
    assert r["linea_liberacion"] is None


def test_pf_compensar_y_reintegro(db: Session):
    c = _cats(db)
    u = _crear_usuario(db, "pf_comp@argentum.com", Moneda.ARS)
    galicia = _crear_billetera(db, u.id, "Galicia", Moneda.ARS, Decimal("600000"))
    visa = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=galicia.id,
        nombre="Visa Galicia",
        dia_cierre=20,
        dia_vencimiento=1,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(visa)
    db.flush()

    compra_id = None
    for idx, ini in enumerate(CICLOS_CERRADOS):
        _agregar_tx(
            db, u.id, galicia.id, c["Sueldo"],
            TipoTransaccion.INGRESO, Moneda.ARS, Decimal("1000000"), ini,
        )
        # Leve variación en Supermercado para que el colchón sea positivo (~10.000)
        super_m = Decimal("600000") if idx % 2 == 0 else Decimal("590000")
        _agregar_tx(
            db, u.id, galicia.id, c["Supermercado"],
            TipoTransaccion.EGRESO, Moneda.ARS, super_m, ini.replace(day=10),
        )
        tx_del = _agregar_tx(
            db, u.id, galicia.id, c["Delivery"],
            TipoTransaccion.EGRESO, Moneda.ARS, Decimal("120000"), ini.replace(day=14),
            metodo=MetodoPago.CREDITO, tarjeta_id=visa.id,
        )
        if idx == len(CICLOS_CERRADOS) - 1:
            compra_id = tx_del.id
        _agregar_tx(
            db, u.id, galicia.id, c["Kiosco"],
            TipoTransaccion.EGRESO, Moneda.ARS, Decimal("30000"), ini.replace(day=16),
        )

    _agregar_tx(
        db, u.id, galicia.id, c["Sueldo"],
        TipoTransaccion.INGRESO, Moneda.ARS, Decimal("1000000"), date(2026, 11, 1),
    )
    _agregar_tx(
        db, u.id, galicia.id, c["Supermercado"],
        TipoTransaccion.EGRESO, Moneda.ARS, Decimal("50000"), date(2026, 11, 15),
    )
    db.commit()

    # Sin reintegro
    r_sin = calcular_puede_permitirse(
        u.id, 1500000, "cuotas", cantidad_cuotas=6, tiene_interes=False, db=db, hoy=HOY_FIJO
    )
    assert r_sin["resultado"] == "justo"
    assert r_sin["linea_compensar"] is not None
    assert "Delivery" in r_sin["linea_compensar"]
    assert "Alimentación" not in r_sin["linea_compensar"]
    assert "Supermercado" not in r_sin["linea_compensar"]
    assert r_sin["linea_reintegro"] == (
        "Fijate si tu banco o billetera tiene reintegros en Delivery."
    )

    # Con espejo reintegro de $8.000 en Delivery con Visa Galicia
    _agregar_tx(
        db, u.id, galicia.id, c["Delivery"],
        TipoTransaccion.EGRESO, Moneda.ARS, Decimal("-8000"), date(2026, 10, 20),
        metodo=MetodoPago.CREDITO, tarjeta_id=visa.id,
        reintegro_de_id=compra_id, tipo_espejo="reintegro",
    )
    db.commit()

    r_con = calcular_puede_permitirse(
        u.id, 1500000, "cuotas", cantidad_cuotas=6, tiene_interes=False, db=db, hoy=HOY_FIJO
    )
    assert r_con["linea_reintegro"] == (
        "En Delivery ya te devolvieron $8.000 con Visa Galicia: pagá con esa."
    )


def test_pg_datos_viejos(db: Session):
    c = _cats(db)
    u = _crear_usuario(db, "pg_viejos@argentum.com", Moneda.ARS)
    galicia = _crear_billetera(db, u.id, "Galicia", Moneda.ARS, Decimal("5000000"))
    for ini in CICLOS_CERRADOS:
        _agregar_tx(
            db, u.id, galicia.id, c["Sueldo"],
            TipoTransaccion.INGRESO, Moneda.ARS, Decimal("2000000"), ini,
        )
        _agregar_tx(
            db, u.id, galicia.id, c["Supermercado"],
            TipoTransaccion.EGRESO, Moneda.ARS, Decimal("500000"), date(ini.year, ini.month, 8),
        )
    db.commit()

    for modo in ("contado", "cuotas", "mensual"):
        r = calcular_puede_permitirse(
            u.id, 50000, modo, cantidad_cuotas=6 if modo == "cuotas" else 1, db=db, hoy=HOY_FIJO
        )
        assert r["resultado"] != "bien", f"{modo} no puede dar bien con datos viejos"


def test_ph_moneda_principal_dolares(db: Session):
    c = _cats(db)
    u = _crear_usuario(db, "ph_usd@argentum.com", Moneda.USD)
    cta_usd = _crear_billetera(db, u.id, "Cuenta dólares", Moneda.USD, Decimal("5000"))
    for ini in CICLOS_CERRADOS:
        _agregar_tx(
            db, u.id, cta_usd.id, c["Sueldo"],
            TipoTransaccion.INGRESO, Moneda.USD, Decimal("2000"), ini,
        )
        _agregar_tx(
            db, u.id, cta_usd.id, c["Supermercado"],
            TipoTransaccion.EGRESO, Moneda.USD, Decimal("800"), ini.replace(day=10),
        )
    _agregar_tx(
        db, u.id, cta_usd.id, c["Sueldo"],
        TipoTransaccion.INGRESO, Moneda.USD, Decimal("2000"), date(2026, 11, 1),
    )
    _agregar_tx(
        db, u.id, cta_usd.id, c["Supermercado"],
        TipoTransaccion.EGRESO, Moneda.USD, Decimal("100"), date(2026, 11, 15),
    )
    db.commit()

    r = calcular_puede_permitirse(u.id, 1000, "contado", db=db, hoy=HOY_FIJO)
    assert r["moneda"] == "USD"
    assert r["linea_cotizacion"] == "Lo calculé con el dólar oficial de hoy ($1.450)."
    assert "US$" in r["linea_principal"]
    # Salvo la línea de cotización, ningún monto en pesos sin US$
    claves_sin_pesos = (
        "titulo", "linea_principal", "linea_ahorros", "linea_compensar", "linea_reintegro",
    )
    for clave in claves_sin_pesos:
        val = r.get(clave)
        if val and "$" in val:
            assert "US$" in val, f"{clave} tiene monto sin US$: {val}"
