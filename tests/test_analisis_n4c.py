# -*- coding: utf-8 -*-
"""
tests/test_analisis_n4c.py
Pruebas para fase_n4c_arreglos_analisis (TQ1, TQ2, TR1 a TR4).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

import openpyxl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.tarjeta_credito import RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.services.analisis_proyeccion_service import obtener_proyeccion_compromisos
from app.services.analisis_resumen_service import obtener_resumen_analisis
from app.services.analisis_excel_hojas import armar_hoja_kpis, armar_hoja_evolucion


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
                    import uuid
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    import uuid
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


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
        session.close()


def _crear_usuario(
    db: Session,
    ciclo_tipo: CicloTipo = CicloTipo.DIA_FIJO,
    ciclo_param: str = "1",
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:8]}@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=ciclo_tipo,
        ciclo_valor=ciclo_param,
        ciclo_ajuste_direccion=None,
        nombre="Test",
        apellido="Analisis",
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _crear_billetera(
    db: Session, usuario_id: UUID, moneda: Moneda = Moneda.ARS
) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre="Billetera Principal",
        moneda=moneda,
        saldo_actual=Decimal("1000000"),
        saldo_inicial=Decimal("0"),
        es_principal=True,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _crear_tarjeta(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    percepcion: Decimal = Decimal("0"),
) -> TarjetaCredito:
    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        nombre="Tarjeta Visa",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=10,
        percepcion_moneda_extranjera=percepcion,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _crear_cotizacion(
    db: Session, fecha: date, venta: Decimal = Decimal("1000")
) -> CotizacionDolar:
    c = CotizacionDolar(
        id=uuid4(),
        fecha=fecha,
        tipo="oficial",
        compra=venta,
        venta=venta,
        promedio=venta,
    )
    db.add(c)
    db.commit()
    return c


def _crear_tx(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    tipo: TipoTransaccion,
    monto: Decimal,
    fecha: date,
    moneda: Moneda = Moneda.ARS,
    tarjeta_id: Optional[UUID] = None,
    categoria_id: Optional[UUID] = None,
    subcategoria_id: Optional[UUID] = None,
    descripcion: str = "",
    suscripcion_id: Optional[UUID] = None,
    grupo_cuotas_id: Optional[UUID] = None,
    es_padre_cuotas: bool = False,
    es_cuota_hija: bool = False,
    origen: OrigenTransaccion = OrigenTransaccion.MANUAL,
) -> Transaccion:
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        tipo=tipo,
        monto=monto,
        moneda=moneda,
        fecha=fecha,
        descripcion=descripcion,
        suscripcion_id=suscripcion_id,
        grupo_cuotas_id=grupo_cuotas_id,
        origen=origen,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=es_padre_cuotas,
        es_cuota_hija=es_cuota_hija,
    )
    db.add(tx)
    return tx


def test_tq1_proyeccion_pesos(db: Session):
    hoy = date(2026, 11, 14)
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, user.id, Moneda.ARS)
    tarjeta = _crear_tarjeta(db, user.id, billetera.id)
    _crear_cotizacion(db, hoy, venta=Decimal("1000"))

    # G1: "Zapatillas" 1 cuota de 9.000 que vence el 10/12/2026 (sin suscripción)
    tx1 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("9000"), date(2026, 11, 10), tarjeta_id=tarjeta.id,
        descripcion="Zapatillas padre",
    )
    db.flush()
    g1 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx1.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Zapatillas",
        cantidad_cuotas=1,
        monto_total=Decimal("9000"),
        total_financiado=Decimal("9000"),
    )
    db.add(g1)
    db.flush()
    c1 = Cuota(
        id=uuid4(),
        grupo_id=g1.id,
        transaccion_id=tx1.id,
        numero_cuota=1,
        monto_proyectado=Decimal("9000"),
        fecha_vencimiento=date(2026, 12, 10),
        pagada=False,
    )
    db.add(c1)

    # G2: suscripción activa en 3 cuotas de 4.000 (10/12/2026)
    sub = Suscripcion(
        id=uuid4(),
        usuario_id=user.id,
        nombre="Spotify",
        proximo_cobro=date(2026, 12, 10),
        estado=EstadoSuscripcion.ACTIVA,
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
    )
    db.add(sub)
    db.flush()
    tx2 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("4000"), date(2026, 11, 10), tarjeta_id=tarjeta.id,
        descripcion="Spotify", suscripcion_id=sub.id,
        origen=OrigenTransaccion.RECURRENTE,
    )
    db.flush()
    g2 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx2.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Spotify Mensual",
        cantidad_cuotas=3,
        monto_total=Decimal("12000"),
        total_financiado=Decimal("12000"),
    )
    db.add(g2)
    db.flush()
    c2 = Cuota(
        id=uuid4(),
        grupo_id=g2.id,
        transaccion_id=tx2.id,
        numero_cuota=1,
        monto_proyectado=Decimal("4000"),
        fecha_vencimiento=date(2026, 12, 10),
        pagada=False,
    )
    db.add(c2)

    # G3: "Silla" 3 cuotas de 10.000 que vencen el 10/08, 10/09 y 10/10/2026, ninguna pagada
    tx3 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("30000"), date(2026, 7, 10), tarjeta_id=tarjeta.id,
        descripcion="Silla",
    )
    db.flush()
    g3 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx3.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Silla",
        cantidad_cuotas=3,
        monto_total=Decimal("30000"),
        total_financiado=Decimal("30000"),
    )
    db.add(g3)
    db.flush()
    for n, mes in [(1, 8), (2, 9), (3, 10)]:
        db.add(Cuota(
            id=uuid4(),
            grupo_id=g3.id,
            transaccion_id=tx3.id,
            numero_cuota=n,
            monto_proyectado=Decimal("10000"),
            fecha_vencimiento=date(2026, mes, 10),
            pagada=False,
        ))

    # G4: "Heladera" 6 cuotas de 20.000 (20 de jul, ago, sep, oct, nov, dic 2026), pagadas 1 y 2
    tx4 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("120000"), date(2026, 6, 20), tarjeta_id=tarjeta.id,
        descripcion="Heladera",
    )
    db.flush()
    g4 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx4.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Heladera",
        cantidad_cuotas=6,
        monto_total=Decimal("120000"),
        total_financiado=Decimal("120000"),
    )
    db.add(g4)
    db.flush()
    for n, mes in [(1, 7), (2, 8), (3, 9), (4, 10), (5, 11), (6, 12)]:
        db.add(Cuota(
            id=uuid4(),
            grupo_id=g4.id,
            transaccion_id=tx4.id,
            numero_cuota=n,
            monto_proyectado=Decimal("20000"),
            fecha_vencimiento=date(2026, mes, 20),
            pagada=(n in (1, 2)),
        ))

    # G6: 3 cuotas de 5.000 (05/12/2026, 05/01/2027, 05/02/2027),
    # desc "  ", padre "" sin cat ni subcat
    tx6 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("15000"), date(2026, 11, 5), tarjeta_id=tarjeta.id,
        descripcion="",
    )
    db.flush()
    g6 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx6.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="  ",
        cantidad_cuotas=3,
        monto_total=Decimal("15000"),
        total_financiado=Decimal("15000"),
    )
    db.add(g6)
    db.flush()
    for n, (y, m) in [(1, (2026, 12)), (2, (2027, 1)), (3, (2027, 2))]:
        db.add(Cuota(
            id=uuid4(),
            grupo_id=g6.id,
            transaccion_id=tx6.id,
            numero_cuota=n,
            monto_proyectado=Decimal("5000"),
            fecha_vencimiento=date(y, m, 5),
            pagada=False,
        ))

    db.commit()

    res = obtener_proyeccion_compromisos(db, user, moneda_modo="solo_ars", meses=6, hoy=hoy)
    planes = res["planes_activos"]

    # planes_activos con exactamente G4 y G6 (por nombre: "Heladera" y "Compra en cuotas")
    nombres = {p["nombre"] for p in planes}
    assert nombres == {"Heladera", "Compra en cuotas"}
    assert len(planes) == 2

    # G4 con cuotas_restantes 2, cantidad_cuotas 6, saldo_pendiente 40000.0,
    # ultimo_vencimiento "2026-12-20"
    p_g4 = next(p for p in planes if p["nombre"] == "Heladera")
    assert p_g4["cuotas_restantes"] == 2
    assert p_g4["cantidad_cuotas"] == 6
    assert p_g4["saldo_pendiente"] == 40000.0
    assert p_g4["ultimo_vencimiento"] == "2026-12-20"

    # G6 con cuotas_restantes 3 y saldo_pendiente 15000.0
    p_g6 = next(p for p in planes if p["nombre"] == "Compra en cuotas")
    assert p_g6["cuotas_restantes"] == 3
    assert p_g6["saldo_pendiente"] == 15000.0

    # kpis.cantidad_planes_activos 2
    assert res["kpis"]["cantidad_planes_activos"] == 2

    # serie_mensual monto_cuotas por ciclo: nov 2026 20000.0, dic 25000.0,
    # ene 5000.0, feb 5000.0, mar 0.0, abr 0.0
    monto_cuotas_serie = [c["monto_cuotas"] for c in res["serie_mensual"]]
    assert monto_cuotas_serie == [20000.0, 25000.0, 5000.0, 5000.0, 0.0, 0.0]

    # kpis.total_cuotas_ventana 55000.0
    assert res["kpis"]["total_cuotas_ventana"] == 55000.0

    # ningún plan con ultimo_vencimiento anterior a 2026-11-14 ni con cantidad_cuotas 1
    for p in planes:
        assert p["ultimo_vencimiento"] >= "2026-11-14"
        assert p["cantidad_cuotas"] > 1


def test_tq2_proyeccion_dolares_y_mixto(db: Session):
    hoy = date(2026, 11, 14)
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera = _crear_billetera(db, user.id, Moneda.ARS)
    tarjeta = _crear_tarjeta(db, user.id, billetera.id)
    _crear_cotizacion(db, hoy, venta=Decimal("1000"))

    # Cargar los planes de TQ1 (G4 Heladera y G6 Compra en cuotas)
    tx4 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("120000"), date(2026, 6, 20), tarjeta_id=tarjeta.id,
        descripcion="Heladera",
    )
    db.flush()
    g4 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx4.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Heladera",
        cantidad_cuotas=6,
        monto_total=Decimal("120000"),
        total_financiado=Decimal("120000"),
    )
    db.add(g4)
    db.flush()
    for n, mes in [(1, 7), (2, 8), (3, 9), (4, 10), (5, 11), (6, 12)]:
        db.add(Cuota(
            id=uuid4(),
            grupo_id=g4.id,
            transaccion_id=tx4.id,
            numero_cuota=n,
            monto_proyectado=Decimal("20000"),
            fecha_vencimiento=date(2026, mes, 20),
            pagada=(n in (1, 2)),
        ))

    tx6 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("15000"), date(2026, 11, 5), tarjeta_id=tarjeta.id,
        descripcion="",
    )
    db.flush()
    g6 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx6.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="  ",
        cantidad_cuotas=3,
        monto_total=Decimal("15000"),
        total_financiado=Decimal("15000"),
    )
    db.add(g6)
    db.flush()
    for n, (y, m) in [(1, (2026, 12)), (2, (2027, 1)), (3, (2027, 2))]:
        db.add(Cuota(
            id=uuid4(),
            grupo_id=g6.id,
            transaccion_id=tx6.id,
            numero_cuota=n,
            monto_proyectado=Decimal("5000"),
            fecha_vencimiento=date(y, m, 5),
            pagada=False,
        ))

    # Sumar G5: 12 cuotas de 50 dólares que vencen el 25 de cada mes desde nov 2026 hasta oct 2027,
    # ninguna pagada, grupo y padre con descripción vacía y padre con subcategoría "Electrónica".
    cat_tec = Categoria(
        id=uuid4(),
        nombre="Tecnología",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat_tec)
    db.flush()
    sub_elec = Subcategoria(
        id=uuid4(),
        categoria_id=cat_tec.id,
        nombre="Electrónica",
    )
    db.add(sub_elec)
    db.flush()

    tx5 = _crear_tx(
        db, user.id, billetera.id, TipoTransaccion.EGRESO,
        Decimal("600"), date(2026, 11, 1), Moneda.USD,
        tarjeta_id=tarjeta.id, categoria_id=cat_tec.id,
        subcategoria_id=sub_elec.id, descripcion="",
    )
    db.flush()
    g5 = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx5.id,
        moneda=Moneda.USD,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="",
        cantidad_cuotas=12,
        monto_total=Decimal("600"),
        total_financiado=Decimal("600"),
    )
    db.add(g5)
    db.flush()

    fechas_g5 = [
        date(2026, 11, 25),
        date(2026, 12, 25),
        date(2027, 1, 25),
        date(2027, 2, 25),
        date(2027, 3, 25),
        date(2027, 4, 25),
        date(2027, 5, 25),
        date(2027, 6, 25),
        date(2027, 7, 25),
        date(2027, 8, 25),
        date(2027, 9, 25),
        date(2027, 10, 25),
    ]
    for idx, f_venc in enumerate(fechas_g5, start=1):
        db.add(Cuota(
            id=uuid4(),
            grupo_id=g5.id,
            transaccion_id=tx5.id,
            numero_cuota=idx,
            monto_proyectado=Decimal("50"),
            fecha_vencimiento=f_venc,
            pagada=False,
        ))

    db.commit()

    # Con solo_usd: planes_activos exactamente G5 con nombre "Electrónica", cuotas_restantes 12,
    # saldo_pendiente 600.0 y monto_cuotas 50.0 en cada uno de los 6 ciclos
    # (total_cuotas_ventana 300.0).
    res_usd = obtener_proyeccion_compromisos(db, user, moneda_modo="solo_usd", meses=6, hoy=hoy)
    planes_usd = res_usd["planes_activos"]
    assert len(planes_usd) == 1
    p_g5 = planes_usd[0]
    assert p_g5["nombre"] == "Electrónica"
    assert p_g5["cuotas_restantes"] == 12
    assert p_g5["saldo_pendiente"] == 600.0
    monto_cuotas_usd = [c["monto_cuotas"] for c in res_usd["serie_mensual"]]
    assert monto_cuotas_usd == [50.0, 50.0, 50.0, 50.0, 50.0, 50.0]
    assert res_usd["kpis"]["total_cuotas_ventana"] == 300.0

    # Con consolidado_ars: los nombres de planes_activos son exactamente
    # {"Heladera", "Compra en cuotas", "Electrónica"}.
    res_cons = obtener_proyeccion_compromisos(
        db, user, moneda_modo="consolidado_ars", meses=6, hoy=hoy
    )
    nombres_cons = {p["nombre"] for p in res_cons["planes_activos"]}
    assert nombres_cons == {"Heladera", "Compra en cuotas", "Electrónica"}

    # Con solo_ars: G5 no aparece.
    res_ars = obtener_proyeccion_compromisos(db, user, moneda_modo="solo_ars", meses=6, hoy=hoy)
    nombres_ars = {p["nombre"] for p in res_ars["planes_activos"]}
    assert "Electrónica" not in nombres_ars
    assert nombres_ars == {"Heladera", "Compra en cuotas"}


def _cargar_datos_tr1(
    db: Session,
) -> tuple[Usuario, Billetera, Billetera, TarjetaCredito, Categoria, Categoria]:
    user = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    billetera_a = _crear_billetera(db, user.id, Moneda.ARS)
    billetera_b = Billetera(
        id=uuid4(),
        usuario_id=user.id,
        nombre="Billetera B",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000"),
        saldo_inicial=Decimal("0"),
        es_principal=False,
    )
    db.add(billetera_b)
    db.flush()
    tarjeta = _crear_tarjeta(db, user.id, billetera_a.id)

    cat_super = Categoria(
        id=uuid4(), nombre="Supermercado", tipo=TipoCategoria.EGRESO
    )
    cat_otros = Categoria(
        id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO
    )
    db.add_all([cat_super, cat_otros])
    db.flush()

    # 1. Ingreso 1.000.000 en A
    _crear_tx(
        db, user.id, billetera_a.id, TipoTransaccion.INGRESO,
        Decimal("1000000"), date(2026, 10, 5), descripcion="Sueldo",
    )

    # 2. Cuota de tarjeta 80.000 (hija de un plan de 3 cuotas) en A
    tx_padre = _crear_tx(
        db, user.id, billetera_a.id, TipoTransaccion.EGRESO,
        Decimal("240000"), date(2026, 10, 1), tarjeta_id=tarjeta.id,
        descripcion="Plan 3 cuotas padre", es_padre_cuotas=True,
    )
    db.flush()
    g_cuotas = GrupoCuotas(
        id=uuid4(),
        usuario_id=user.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx_padre.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Plan 3 cuotas",
        cantidad_cuotas=3,
        monto_total=Decimal("240000"),
        total_financiado=Decimal("240000"),
    )
    db.add(g_cuotas)
    db.flush()
    tx_padre.grupo_cuotas_id = g_cuotas.id

    tx_cuota = _crear_tx(
        db, user.id, billetera_a.id, TipoTransaccion.EGRESO,
        Decimal("80000"), date(2026, 10, 10), tarjeta_id=tarjeta.id,
        descripcion="Cuota 1", grupo_cuotas_id=g_cuotas.id,
        es_cuota_hija=True,
    )
    db.flush()
    db.add(Cuota(
        id=uuid4(),
        grupo_id=g_cuotas.id,
        transaccion_id=tx_cuota.id,
        numero_cuota=1,
        monto_proyectado=Decimal("80000"),
        fecha_vencimiento=date(2026, 10, 10),
        pagada=True,
    ))

    # 3. Gasto fijo 200.000 (con suscripcion_id) en A
    sub = Suscripcion(
        id=uuid4(),
        usuario_id=user.id,
        nombre="Alquiler",
        proximo_cobro=date(2026, 11, 10),
        estado=EstadoSuscripcion.ACTIVA,
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        billetera_id=billetera_a.id,
    )
    db.add(sub)
    db.flush()
    _crear_tx(
        db, user.id, billetera_a.id, TipoTransaccion.EGRESO,
        Decimal("200000"), date(2026, 10, 10), descripcion="Alquiler mensual",
        suscripcion_id=sub.id, origen=OrigenTransaccion.RECURRENTE,
    )

    # 4. Gastos variables: Supermercado 150.000 en B y Otros 220.000 en A
    _crear_tx(
        db, user.id, billetera_b.id, TipoTransaccion.EGRESO,
        Decimal("150000"), date(2026, 10, 15), categoria_id=cat_super.id,
        descripcion="Supermercado B",
    )
    _crear_tx(
        db, user.id, billetera_a.id, TipoTransaccion.EGRESO,
        Decimal("220000"), date(2026, 10, 20), categoria_id=cat_otros.id,
        descripcion="Otros gastos A",
    )
    db.commit()
    return user, billetera_a, billetera_b, tarjeta, cat_super, cat_otros


def test_tr1_numeros_principales_pesos(db: Session):
    hoy = date(2026, 11, 14)
    user, billetera_a, billetera_b, tarjeta, cat_super, cat_otros = (
        _cargar_datos_tr1(db)
    )

    # 1. Sin filtros
    res_base = obtener_resumen_analisis(db, user, cantidad_ciclos=2, hoy=hoy)
    k_base = res_base["kpis"]
    assert k_base["solo_gastos"] is False
    assert k_base["ingresos_totales"] == 1000000.0
    assert k_base["gastos_totales"] == 650000.0
    assert k_base["balance_neto"] == 350000.0
    assert k_base["tasa_ahorro_pct"] == 35.0
    assert k_base["gasto_total_referencia"] is None
    assert k_base["peso_sobre_gasto_total_pct"] is None

    # 2. Naturaleza "cuotas"
    res_cuotas = obtener_resumen_analisis(
        db, user, cantidad_ciclos=2, naturaleza="cuotas", hoy=hoy
    )
    k_cuotas = res_cuotas["kpis"]
    assert k_cuotas["solo_gastos"] is True
    assert k_cuotas["ingresos_totales"] is None
    assert k_cuotas["balance_neto"] is None
    assert k_cuotas["tasa_ahorro_pct"] is None
    assert k_cuotas["gastos_totales"] == 80000.0
    assert k_cuotas["gasto_total_referencia"] == 650000.0
    assert k_cuotas["peso_sobre_gasto_total_pct"] == 12.3

    # 3. Naturaleza "fijos"
    res_fijos = obtener_resumen_analisis(
        db, user, cantidad_ciclos=2, naturaleza="fijos", hoy=hoy
    )
    k_fijos = res_fijos["kpis"]
    assert k_fijos["solo_gastos"] is True
    assert k_fijos["gastos_totales"] == 200000.0
    assert k_fijos["gasto_total_referencia"] == 650000.0
    assert k_fijos["peso_sobre_gasto_total_pct"] == 30.8

    # 4. Naturaleza "variables"
    res_vars = obtener_resumen_analisis(
        db, user, cantidad_ciclos=2, naturaleza="variables", hoy=hoy
    )
    k_vars = res_vars["kpis"]
    assert k_vars["solo_gastos"] is True
    assert k_vars["gastos_totales"] == 370000.0
    assert k_vars["gasto_total_referencia"] == 650000.0
    assert k_vars["peso_sobre_gasto_total_pct"] == 56.9

    # 5. Categoría Supermercado
    res_cat = obtener_resumen_analisis(
        db, user, cantidad_ciclos=2, categoria_id=cat_super.id, hoy=hoy
    )
    k_cat = res_cat["kpis"]
    assert k_cat["solo_gastos"] is True
    assert k_cat["gastos_totales"] == 150000.0
    assert k_cat["gasto_total_referencia"] == 650000.0
    assert k_cat["peso_sobre_gasto_total_pct"] == 23.1

    # 6. Billetera B sola
    res_bb = obtener_resumen_analisis(
        db, user, cantidad_ciclos=2, billetera_id=billetera_b.id, hoy=hoy
    )
    k_bb = res_bb["kpis"]
    assert k_bb["solo_gastos"] is False
    assert k_bb["gastos_totales"] == 150000.0
    assert k_bb["ingresos_totales"] == 0.0

    # 7. Billetera A + "cuotas"
    res_a_cuotas = obtener_resumen_analisis(
        db,
        user,
        cantidad_ciclos=2,
        billetera_id=billetera_a.id,
        naturaleza="cuotas",
        hoy=hoy,
    )
    k_a_cuotas = res_a_cuotas["kpis"]
    assert k_a_cuotas["solo_gastos"] is True
    assert k_a_cuotas["gastos_totales"] == 80000.0
    assert k_a_cuotas["gasto_total_referencia"] == 500000.0
    assert k_a_cuotas["peso_sobre_gasto_total_pct"] == 16.0

    # 8. Billetera B + "variables"
    res_b_vars = obtener_resumen_analisis(
        db,
        user,
        cantidad_ciclos=2,
        billetera_id=billetera_b.id,
        naturaleza="variables",
        hoy=hoy,
    )
    k_b_vars = res_b_vars["kpis"]
    assert k_b_vars["solo_gastos"] is True
    assert k_b_vars["gastos_totales"] == 150000.0
    assert k_b_vars["gasto_total_referencia"] == 150000.0
    assert k_b_vars["peso_sobre_gasto_total_pct"] == 100.0


def test_tr2_numeros_principales_dolares_y_mixto(db: Session):
    hoy = date(2026, 11, 14)
    # Usuario USD con valores divididos por 1.000
    user_usd = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_usd_a = _crear_billetera(db, user_usd.id, Moneda.USD)
    b_usd_b = Billetera(
        id=uuid4(),
        usuario_id=user_usd.id,
        nombre="Billetera USD B",
        moneda=Moneda.USD,
        saldo_actual=Decimal("50000"),
        saldo_inicial=Decimal("0"),
        es_principal=False,
    )
    db.add(b_usd_b)
    db.flush()
    tarjeta_usd = _crear_tarjeta(db, user_usd.id, b_usd_a.id)

    # Ingreso 1.000 USD
    _crear_tx(
        db, user_usd.id, b_usd_a.id, TipoTransaccion.INGRESO,
        Decimal("1000"), date(2026, 10, 5), Moneda.USD,
        descripcion="Sueldo USD",
    )

    # Cuota 80 USD
    tx_padre_u = _crear_tx(
        db, user_usd.id, b_usd_a.id, TipoTransaccion.EGRESO,
        Decimal("240"), date(2026, 10, 1), Moneda.USD,
        tarjeta_id=tarjeta_usd.id, descripcion="Plan USD 3 cuotas",
        es_padre_cuotas=True,
    )
    db.flush()
    g_cuotas_u = GrupoCuotas(
        id=uuid4(),
        usuario_id=user_usd.id,
        tarjeta_id=tarjeta_usd.id,
        transaccion_padre_id=tx_padre_u.id,
        moneda=Moneda.USD,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Plan USD",
        cantidad_cuotas=3,
        monto_total=Decimal("240"),
        total_financiado=Decimal("240"),
    )
    db.add(g_cuotas_u)
    db.flush()
    tx_padre_u.grupo_cuotas_id = g_cuotas_u.id

    tx_cuota_u = _crear_tx(
        db, user_usd.id, b_usd_a.id, TipoTransaccion.EGRESO,
        Decimal("80"), date(2026, 10, 10), Moneda.USD,
        tarjeta_id=tarjeta_usd.id, descripcion="Cuota 1 USD",
        grupo_cuotas_id=g_cuotas_u.id, es_cuota_hija=True,
    )
    db.flush()
    db.add(Cuota(
        id=uuid4(),
        grupo_id=g_cuotas_u.id,
        transaccion_id=tx_cuota_u.id,
        numero_cuota=1,
        monto_proyectado=Decimal("80"),
        fecha_vencimiento=date(2026, 10, 10),
        pagada=True,
    ))

    # Gasto fijo 200 USD
    sub_u = Suscripcion(
        id=uuid4(),
        usuario_id=user_usd.id,
        nombre="Servicio USD",
        proximo_cobro=date(2026, 11, 10),
        estado=EstadoSuscripcion.ACTIVA,
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        billetera_id=b_usd_a.id,
    )
    db.add(sub_u)
    db.flush()
    _crear_tx(
        db, user_usd.id, b_usd_a.id, TipoTransaccion.EGRESO,
        Decimal("200"), date(2026, 10, 10), Moneda.USD,
        descripcion="Servicio fijo USD", suscripcion_id=sub_u.id,
        origen=OrigenTransaccion.RECURRENTE,
    )

    # Variables: 150 en B y 220 en A
    _crear_tx(
        db, user_usd.id, b_usd_b.id, TipoTransaccion.EGRESO,
        Decimal("150"), date(2026, 10, 15), Moneda.USD,
        descripcion="Variable B USD",
    )
    _crear_tx(
        db, user_usd.id, b_usd_a.id, TipoTransaccion.EGRESO,
        Decimal("220"), date(2026, 10, 20), Moneda.USD,
        descripcion="Variable A USD",
    )
    db.commit()

    # con solo_usd y "cuotas" -> 80.0, referencia 650.0, peso 12.3
    res_u_cuotas = obtener_resumen_analisis(
        db,
        user_usd,
        moneda_modo="solo_usd",
        cantidad_ciclos=2,
        naturaleza="cuotas",
        hoy=hoy,
    )
    k_u_cuotas = res_u_cuotas["kpis"]
    assert k_u_cuotas["solo_gastos"] is True
    assert k_u_cuotas["gastos_totales"] == 80.0
    assert k_u_cuotas["gasto_total_referencia"] == 650.0
    assert k_u_cuotas["peso_sobre_gasto_total_pct"] == 12.3

    # Usuario mixto: los movimientos en pesos de TR1 más un gasto variable de 100 dólares
    # en el mismo ciclo, con cotización oficial cargada en el fixture de 1.000 para esa fecha.
    user_m = _crear_usuario(db, CicloTipo.DIA_FIJO, "1")
    b_m_ars = _crear_billetera(db, user_m.id, Moneda.ARS)
    b_m_usd = Billetera(
        id=uuid4(),
        usuario_id=user_m.id,
        nombre="Billetera USD Mixta",
        moneda=Moneda.USD,
        saldo_actual=Decimal("10000"),
        saldo_inicial=Decimal("0"),
        es_principal=False,
    )
    db.add(b_m_usd)
    db.flush()
    tarjeta_m = _crear_tarjeta(db, user_m.id, b_m_ars.id)

    # Cotización 1.000 para las fechas relevantes
    _crear_cotizacion(db, date(2026, 10, 1), venta=Decimal("1000"))
    _crear_cotizacion(db, date(2026, 10, 5), venta=Decimal("1000"))
    _crear_cotizacion(db, date(2026, 10, 10), venta=Decimal("1000"))
    _crear_cotizacion(db, date(2026, 10, 15), venta=Decimal("1000"))
    _crear_cotizacion(db, date(2026, 10, 20), venta=Decimal("1000"))

    # Ingreso 1.000.000 ARS
    db.add(Transaccion(
        id=uuid4(),
        usuario_id=user_m.id,
        billetera_id=b_m_ars.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("1000000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        descripcion="Sueldo Mixto",
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
    ))

    # Cuota 80.000 ARS
    tx_padre_m = Transaccion(
        id=uuid4(),
        usuario_id=user_m.id,
        billetera_id=b_m_ars.id,
        tarjeta_id=tarjeta_m.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("240000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 1),
        descripcion="Plan Mixto 3 cuotas",
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=True,
        es_cuota_hija=False,
    )
    db.add(tx_padre_m)
    db.flush()
    g_cuotas_m = GrupoCuotas(
        id=uuid4(),
        usuario_id=user_m.id,
        tarjeta_id=tarjeta_m.id,
        transaccion_padre_id=tx_padre_m.id,
        moneda=Moneda.ARS,
        estado=EstadoGrupoCuotas.ACTIVO,
        descripcion="Plan Mixto",
        cantidad_cuotas=3,
        monto_total=Decimal("240000"),
        total_financiado=Decimal("240000"),
    )
    db.add(g_cuotas_m)
    db.flush()
    tx_padre_m.grupo_cuotas_id = g_cuotas_m.id

    tx_cuota_m = Transaccion(
        id=uuid4(),
        usuario_id=user_m.id,
        billetera_id=b_m_ars.id,
        tarjeta_id=tarjeta_m.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("80000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Cuota 1 Mixto",
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        grupo_cuotas_id=g_cuotas_m.id,
    )
    db.add(tx_cuota_m)
    db.flush()
    db.add(Cuota(
        id=uuid4(),
        grupo_id=g_cuotas_m.id,
        transaccion_id=tx_cuota_m.id,
        numero_cuota=1,
        monto_proyectado=Decimal("80000"),
        fecha_vencimiento=date(2026, 10, 10),
        pagada=True,
    ))

    # Fijo 200.000 ARS
    sub_m = Suscripcion(
        id=uuid4(),
        usuario_id=user_m.id,
        nombre="Fijo Mixto",
        proximo_cobro=date(2026, 11, 10),
        estado=EstadoSuscripcion.ACTIVA,
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        billetera_id=b_m_ars.id,
    )
    db.add(sub_m)
    db.flush()
    db.add(Transaccion(
        id=uuid4(),
        usuario_id=user_m.id,
        billetera_id=b_m_ars.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("200000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Fijo Mixto",
        suscripcion_id=sub_m.id,
        origen=OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
    ))

    # Variables ARS: 150.000 + 220.000 = 370.000 ARS
    db.add(Transaccion(
        id=uuid4(),
        usuario_id=user_m.id,
        billetera_id=b_m_ars.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("150000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Variable 1 Mixto",
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
    ))
    db.add(Transaccion(
        id=uuid4(),
        usuario_id=user_m.id,
        billetera_id=b_m_ars.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("220000"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 20),
        descripcion="Variable 2 Mixto",
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
    ))

    # Gasto variable de 100 dólares en el mismo ciclo
    db.add(Transaccion(
        id=uuid4(),
        usuario_id=user_m.id,
        billetera_id=b_m_usd.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 15),
        descripcion="Variable 100 USD",
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_padre_cuotas=False,
        es_cuota_hija=False,
    ))
    db.commit()

    # con consolidado_ars y "variables" -> gastos 470000.0, referencia 750000.0, peso 62.7
    res_m_vars = obtener_resumen_analisis(
        db,
        user_m,
        moneda_modo="consolidado_ars",
        cantidad_ciclos=2,
        naturaleza="variables",
        hoy=hoy,
    )
    k_m_vars = res_m_vars["kpis"]
    assert k_m_vars["solo_gastos"] is True
    assert k_m_vars["gastos_totales"] == 470000.0
    assert k_m_vars["gasto_total_referencia"] == 750000.0
    assert k_m_vars["peso_sobre_gasto_total_pct"] == 62.7

    # con consolidado_ars y "cuotas" -> 80000.0, 750000.0, 10.7
    res_m_cuotas = obtener_resumen_analisis(
        db,
        user_m,
        moneda_modo="consolidado_ars",
        cantidad_ciclos=2,
        naturaleza="cuotas",
        hoy=hoy,
    )
    k_m_cuotas = res_m_cuotas["kpis"]
    assert k_m_cuotas["solo_gastos"] is True
    assert k_m_cuotas["gastos_totales"] == 80000.0
    assert k_m_cuotas["gasto_total_referencia"] == 750000.0
    assert k_m_cuotas["peso_sobre_gasto_total_pct"] == 10.7


def test_tr3_excel(db: Session):
    hoy = date(2026, 11, 14)
    user, _, _, _, cat_super, _ = _cargar_datos_tr1(db)

    # Libro plantilla "Gastos del día a día" (naturaleza="variables")
    res_vars = obtener_resumen_analisis(
        db, user, cantidad_ciclos=2, naturaleza="variables", hoy=hoy
    )
    wb_vars = openpyxl.Workbook()
    ws_kpis_v = wb_vars.active
    ws_kpis_v.title = "Números principales"
    armar_hoja_kpis(ws_kpis_v, res_vars, "Gastos del día a día", "Filtros: variables")

    ws_evol_v = wb_vars.create_sheet("Evolución")
    armar_hoja_evolucion(ws_evol_v, res_vars, "Gastos del día a día", "Filtros: variables")

    # la hoja Números principales no tiene ninguna celda "Ingresos",
    # "Te quedó" ni "% de lo que entró"
    valores_kpis = [
        cell.value
        for row in ws_kpis_v.iter_rows()
        for cell in row
        if cell.value is not None
    ]
    assert "Ingresos" not in valores_kpis
    assert "Te quedó" not in valores_kpis
    assert "% de lo que entró" not in valores_kpis

    # A5 "Egresos", A6 "Todo lo que gastaste", A7 "% de todo lo que gastaste" con B7 formula,
    # A8 "Promedio de gastos por ciclo"
    assert ws_kpis_v["A5"].value == "Egresos"
    assert ws_kpis_v["A6"].value == "Todo lo que gastaste"
    assert ws_kpis_v["A7"].value == "% de todo lo que gastaste"
    assert ws_kpis_v["B7"].value == '=IF(B6>0,B5/B6,"")'
    assert ws_kpis_v["A8"].value == "Promedio de gastos por ciclo"

    # hoja Evolución con encabezados exactos ["Ciclo", "Desde", "Hasta", "Gastos", "Estado"]
    headers_evol = [ws_evol_v.cell(row=4, column=c).value for c in range(1, 6)]
    assert headers_evol == ["Ciclo", "Desde", "Hasta", "Gastos", "Estado"]
    assert ws_evol_v.cell(row=4, column=6).value is None

    # Libro sin filtros (naturaleza "todos"): exactamente las filas de hoy
    res_base = obtener_resumen_analisis(db, user, cantidad_ciclos=2, hoy=hoy)
    wb_base = openpyxl.Workbook()
    ws_kpis_b = wb_base.active
    ws_kpis_b.title = "Números principales"
    armar_hoja_kpis(ws_kpis_b, res_base, "Resumen del ciclo", "Filtros: todos")

    assert ws_kpis_b["A5"].value == "Ingresos"
    assert ws_kpis_b["A6"].value == "Egresos"
    assert ws_kpis_b["A7"].value == "Te quedó"
    assert ws_kpis_b["B7"].value == "=B5-B6"
    assert ws_kpis_b["A8"].value == "% de lo que entró"
    assert ws_kpis_b["A9"].value == "Promedio de gastos por ciclo"

    # Estático sobre analisis_excel_hojas.py
    backend_root = Path(__file__).resolve().parent.parent
    hojas_path = backend_root / "app" / "services" / "analisis_excel_hojas.py"
    hojas_code = hojas_path.read_text(encoding="utf-8")
    assert '"ingresos_totales") or 0.0' not in hojas_code
    assert '"ingresos") or 0.0' not in hojas_code


def test_tr4_estatico():
    backend_root = Path(__file__).resolve().parent.parent
    peso_service = backend_root / "app" / "services" / "analisis_peso_service.py"
    assert peso_service.exists()
    lines_peso = peso_service.read_text(encoding="utf-8").splitlines()
    assert len(lines_peso) <= 120
    for idx, l in enumerate(lines_peso, start=1):
        assert len(l) <= 100, f"analisis_peso_service.py L{idx} supera 100 chars: {l}"

    resumen_path = backend_root / "app" / "services" / "analisis_resumen_service.py"
    resumen_code = resumen_path.read_text(encoding="utf-8")
    assert "bool(categoria_id or tarjeta_id)" not in resumen_code
    assert "es_vista_solo_gastos(" in resumen_code
    lines_resumen = resumen_code.splitlines()
    assert len(lines_resumen) <= 600

    proy_path = backend_root / "app" / "services" / "analisis_proyeccion_service.py"
    proy_code = proy_path.read_text(encoding="utf-8")
    assert "venc < primer_ciclo_inicio" not in proy_code

