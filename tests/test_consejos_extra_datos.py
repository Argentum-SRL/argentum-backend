# -*- coding: utf-8 -*-
"""
tests/test_consejos_extra_datos.py
Pruebas de base de datos en SQLite para consejos C8 a C13, recolección extendida,
medición y consejos cumplidos (fase_i2b_consejos).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.consejo import ConsejoEmitido
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.saldo_arrastrado import EstadoSaldoArrastrado, SaldoArrastradoTarjeta
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import fin_de_mes_service
from app.services.consejos_datos import recolectar
from app.services.consejos_extra import (
    evaluar_arrastre,
    evaluar_candidatos,
    evaluar_extra,
)
from app.services.consejos_extra_datos import recolectar_extra
from app.services.consejos_service import (
    consejo_cumplido,
    emitir_si_corresponde,
    medir_pendientes,
    resolver_cumplidos,
)
from app.services.frescura_service import Frescura
from app.services.resumen_tarjeta_service import calcular_resumen_actual


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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

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
    try:
        yield session
    finally:
        session.close()


def _mock_frescura_cobertura(monkeypatch, hoy: date):
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: 100)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=hoy,
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )


def test_usuario_p_recolectar_extra_y_emision(db: Session, monkeypatch):
    """
    Usuario P (principal ARS, primer movimiento 01/08/2026):
    - Tarjeta 'Visa' ARS activa con percepción 30 y un SaldoArrastradoTarjeta ACTIVO de 120.000 (vence 10/10/2026)
    - Billetera 'Dólares' USD 150 y 'Inversión USD' USD 1.000 de inversión
    - Cotización oficial 05/11/2026 compra 1.400 venta 1.450
    - Meta 'Viaje' ARS objetivo 1.000.000, actual 400.000, fecha_limite 31/03/2027, creada 01/08/2026 con aportes de 50.000 el 10/08, 10/09 y 10/10
    - Trabajo > Aguinaldo 500.000 el 03/11
    """
    hoy = date(2026, 11, 5)
    _mock_frescura_cobertura(monkeypatch, hoy)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.resumen_tarjeta_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.consejos_service.hoy_argentina", lambda: hoy)

    # 1. Usuario P
    p = Usuario(
        id=uuid4(),
        email="usuario_p@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(p)

    # 2. Billeteras
    b_ars = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Cuenta Pesos",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Dólares",
        moneda=Moneda.USD,
        saldo_actual=Decimal("150.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_inv_usd = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Inversión USD",
        moneda=Moneda.USD,
        saldo_actual=Decimal("1000.00"),
        es_inversion=True,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_ars, b_usd, b_inv_usd])

    # 3. Tarjeta Visa
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=p.id,
        billetera_id=b_ars.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        dia_cierre=1,
        dia_vencimiento=10,
        regla_cierre="dia_fijo",
        moneda=Moneda.ARS,
        percepcion_moneda_extranjera=Decimal("30.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)

    # Transacción base para el saldo arrastrado
    tx_orig = Transaccion(
        id=uuid4(),
        usuario_id=p.id,
        billetera_id=b_ars.id,
        tarjeta_id=tarjeta.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add(tx_orig)
    db.flush()

    saldo_arr = SaldoArrastradoTarjeta(
        id=uuid4(),
        tarjeta_id=tarjeta.id,
        fecha_vencimiento_resumen=date(2026, 10, 10),
        monto_inicial=Decimal("120000.00"),
        monto_restante=Decimal("120000.00"),
        moneda=Moneda.ARS,
        estado=EstadoSaldoArrastrado.ACTIVO,
        transaccion_origen_id=tx_orig.id,
    )
    db.add(saldo_arr)

    # 4. Cotización oficial
    cotiz = CotizacionDolar(
        fecha=hoy,
        tipo="oficial",
        compra=Decimal("1400.00"),
        venta=Decimal("1450.00"),
        promedio=Decimal("1425.00"),
    )
    db.add(cotiz)

    # 5. Categorías y subcategorías
    cat_trabajo = Categoria(id=uuid4(), nombre="Trabajo", tipo=TipoCategoria.INGRESO, estado=EstadoCategoria.ACTIVA)
    subcat_ag = Subcategoria(id=uuid4(), categoria_id=cat_trabajo.id, nombre="Aguinaldo", estado=EstadoSubcategoria.ACTIVA)
    db.add_all([cat_trabajo, subcat_ag])
    db.flush()

    # Transacción primer movimiento 01/08/2026
    tx_primera = Transaccion(
        id=uuid4(),
        usuario_id=p.id,
        billetera_id=b_ars.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 1),
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add(tx_primera)

    # Aguinaldo 03/11
    tx_ag = Transaccion(
        id=uuid4(),
        usuario_id=p.id,
        billetera_id=b_ars.id,
        categoria_id=cat_trabajo.id,
        subcategoria_id=subcat_ag.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("500000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 3),
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 3, 11, 0, 0, tzinfo=timezone.utc),
    )
    db.add(tx_ag)

    # 6. Meta "Viaje" creada 01/08/2026
    meta_viaje = Meta(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Viaje",
        monto_objetivo=Decimal("1000000.00"),
        monto_actual=Decimal("400000.00"),
        moneda=Moneda.ARS,
        fecha_limite=date(2027, 3, 31),
        estado=EstadoMeta.ACTIVA,
        fecha_creacion=datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add(meta_viaje)
    db.flush()

    for f_ap in (date(2026, 8, 10), date(2026, 9, 10), date(2026, 10, 10)):
        mov = MovimientoMeta(
            id=uuid4(),
            meta_id=meta_viaje.id,
            billetera_id=b_ars.id,
            tipo=TipoMovimientoMeta.APORTE,
            monto=Decimal("50000.00"),
            moneda_movimiento=Moneda.ARS,
            fecha=f_ap,
            fecha_creacion=datetime(f_ap.year, f_ap.month, f_ap.day, 10, 0, 0, tzinfo=timezone.utc),
        )
        db.add(mov)

    db.commit()

    # Verificar recolectar_extra
    cerrados = [
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 8, 1), date(2026, 8, 31)),
    ]
    ciclo_act = (date(2026, 11, 1), date(2026, 11, 30))
    extra = recolectar_extra(db, p, hoy, cerrados, ciclo_act)

    assert len(extra.saldos) == 1
    assert extra.saldos[0].tarjeta == "Visa"
    assert extra.saldos[0].moneda == "ARS"
    assert extra.saldos[0].monto_restante == Decimal("120000.00")
    assert extra.usd_propios == Decimal("150.00")
    assert extra.cotizacion_venta == Decimal("1450.00")

    assert len(extra.metas) == 1
    assert extra.metas[0].nombre == "Viaje"
    assert extra.metas[0].cantidad_aportes == 3
    assert extra.metas[0].aportes_ciclos == (Decimal("50000.00"), Decimal("50000.00"), Decimal("50000.00"))

    assert len(extra.ingresos_7) == 1
    assert extra.ingresos_7[0].subcategoria == "Aguinaldo"
    assert extra.ingresos_7[0].monto == Decimal("500000.00")

    # Evaluar candidatos de recolectar(db, p, hoy)
    datos = recolectar(db, p, hoy)
    candidatos = evaluar_candidatos(datos)

    textos = [c.texto for c in candidatos]
    assert (
        "Del resumen de Visa te quedaron $120.000 sin pagar: la tarjeta cobra de los intereses más altos. "
        "Si podés, pagalo antes que otros gastos."
    ) in textos
    assert (
        "Te entraron $500.000 de aguinaldo. Si apartás $100.000 ahora para Viaje, no se diluye en el mes."
    ) in textos
    assert (
        "A este ritmo llegás a Viaje en noviembre de 2027, no en marzo de 2027. "
        "Para llegar a tiempo hacen falta unos $150.000 por mes."
    ) in textos

    # emitir_si_corresponde -> emite el arrastre (grupo 0 de ordenar)
    emitido = emitir_si_corresponde(db, p, hoy, inflacion=None)
    assert emitido is not None
    assert emitido.tipo == "arrastre"
    assert (
        emitido.texto
        == "Del resumen de Visa te quedaron $120.000 sin pagar: la tarjeta cobra de los intereses más altos. "
        "Si podés, pagalo antes que otros gastos."
    )


def test_resumen_en_dolares(db: Session, monkeypatch):
    """
    Resumen en dólares: compra de US$100 en 1 pago con tarjeta que vence después de 05/11 con cierre antes de 05/11.
    Días de tarjeta elegidos: dia_cierre=1, dia_vencimiento=10, regla_cierre='dia_fijo'.
    Con hoy=05/11/2026: cierre=01/11/2026, vencimiento=10/11/2026.
    """
    hoy = date(2026, 11, 5)
    _mock_frescura_cobertura(monkeypatch, hoy)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.resumen_tarjeta_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.consejos_service.hoy_argentina", lambda: hoy)

    u = Usuario(
        id=uuid4(),
        email="test_usd_resumen@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u)

    b_ars = Billetera(id=uuid4(), usuario_id=u.id, nombre="ARS", moneda=Moneda.ARS, saldo_actual=Decimal("500000"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    b_usd = Billetera(id=uuid4(), usuario_id=u.id, nombre="USD", moneda=Moneda.USD, saldo_actual=Decimal("150"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    db.add_all([b_ars, b_usd])

    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        dia_cierre=1,
        dia_vencimiento=10,
        regla_cierre="dia_fijo",
        moneda=Moneda.ARS,
        percepcion_moneda_extranjera=Decimal("30.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta)

    cotiz = CotizacionDolar(fecha=hoy, tipo="oficial", compra=Decimal("1400.00"), venta=Decimal("1450.00"), promedio=Decimal("1425.00"))
    db.add(cotiz)
    db.flush()

    # Compra en 1 pago de US$100 con la tarjeta (padre + hija) que cae en el resumen de vencimiento 10/11/2026
    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        tarjeta_id=tarjeta.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 10, 25),
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 25, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add(tx_padre)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Compra USD",
        cantidad_cuotas=1,
        monto_total=Decimal("100.00"),
        total_financiado=Decimal("100.00"),
        moneda=Moneda.USD,
        primer_vencimiento=date(2026, 11, 10),
    )
    db.add(grupo)
    db.flush()

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        tarjeta_id=tarjeta.id,
        grupo_cuotas_id=grupo.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 11, 10),
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 25, 10, 1, 0, tzinfo=timezone.utc),
    )
    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        monto_proyectado=Decimal("100.00"),
        fecha_vencimiento=date(2026, 11, 10),
    )
    db.add_all([tx_hija, cuota])
    db.commit()

    extra = recolectar_extra(db, u, hoy, cerrados=[], ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)))
    assert len(extra.resumenes_usd) == 1
    res_actual = calcular_resumen_actual(db, tarjeta)
    assert extra.resumenes_usd[0].usd_pendiente == res_actual.total_actual_usd

    datos = recolectar(db, u, hoy)
    candidatos = evaluar_candidatos(datos)
    c_dolares = [c for c in candidatos if c.tipo == "dolares"]
    assert len(c_dolares) == 1
    assert (
        c_dolares[0].texto
        == "Si pagás los US$100 del resumen de Visa con tus dólares, evitás unos $43.500 de percepción."
    )


def test_cuotas_proximas(db: Session, monkeypatch):
    """Grupo de 3 cuotas ARS de 50.000 con hijas el 10/12/2026, 10/01/2027 y 10/02/2027."""
    hoy = date(2026, 11, 5)
    _mock_frescura_cobertura(monkeypatch, hoy)

    u = Usuario(
        id=uuid4(),
        email="test_cuotas_prox@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u)

    b_ars = Billetera(id=uuid4(), usuario_id=u.id, nombre="ARS", moneda=Moneda.ARS, saldo_actual=Decimal("100000"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    db.add(b_ars)
    db.flush()

    # Padre de cuotas, grupo de cuotas y transacciones hijas
    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("150000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 1),
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add(tx_padre)
    db.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Compra 3 cuotas",
        cantidad_cuotas=3,
        monto_total=Decimal("150000.00"),
        total_financiado=Decimal("150000.00"),
        moneda=Moneda.ARS,
        primer_vencimiento=date(2026, 12, 10),
    )
    db.add(grupo)
    db.flush()

    fechas = [date(2026, 12, 10), date(2027, 1, 10), date(2027, 2, 10)]
    for f in fechas:
        tx = Transaccion(
            id=uuid4(),
            usuario_id=u.id,
            billetera_id=b_ars.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("50000.00"),
            moneda=Moneda.ARS,
            fecha=f,
            metodo_pago=MetodoPago.CREDITO,
            origen=OrigenTransaccion.MANUAL,
            es_padre_cuotas=False,
            es_cuota_hija=True,
            grupo_cuotas_id=grupo.id,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        db.add(tx)
    db.commit()

    ciclo_act = (date(2026, 11, 1), date(2026, 11, 30))
    extra = recolectar_extra(db, u, hoy, cerrados=[], ciclo_actual=ciclo_act)
    assert extra.cuotas_proximas[0] == (date(2026, 12, 1), {"ARS": Decimal("50000.00"), "USD": Decimal("0")})
    assert extra.cuotas_proximas[1] == (date(2027, 1, 1), {"ARS": Decimal("50000.00"), "USD": Decimal("0")})


def test_usuario_d_y_usuario_m(db: Session, monkeypatch):
    """
    Usuario D (principal USD) con meta USD y aguinaldo US$500 en los últimos 7 días.
    Usuario M (ARS con secundaria USD) con saldo arrastrado en USD de 50 y otro en ARS de 30.000 en la misma tarjeta.
    """
    hoy = date(2026, 11, 5)
    _mock_frescura_cobertura(monkeypatch, hoy)

    # Usuario D
    d_user = Usuario(
        id=uuid4(),
        email="usuario_d@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.USD,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(d_user)

    b_usd_d = Billetera(id=uuid4(), usuario_id=d_user.id, nombre="Caja USD", moneda=Moneda.USD, saldo_actual=Decimal("1000.00"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    cat_trabajo = Categoria(id=uuid4(), nombre="Trabajo D", tipo=TipoCategoria.INGRESO, estado=EstadoCategoria.ACTIVA)
    subcat_ag = Subcategoria(id=uuid4(), categoria_id=cat_trabajo.id, nombre="Aguinaldo", estado=EstadoSubcategoria.ACTIVA)
    db.add_all([b_usd_d, cat_trabajo, subcat_ag])
    db.flush()

    meta_usd_d = Meta(
        id=uuid4(),
        usuario_id=d_user.id,
        nombre="Fondo Retiro",
        monto_objetivo=Decimal("10000.00"),
        monto_actual=Decimal("500.00"),
        moneda=Moneda.USD,
        fecha_limite=date(2028, 1, 1),
        estado=EstadoMeta.ACTIVA,
        fecha_creacion=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    tx_ag_d = Transaccion(
        id=uuid4(),
        usuario_id=d_user.id,
        billetera_id=b_usd_d.id,
        categoria_id=cat_trabajo.id,
        subcategoria_id=subcat_ag.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.USD,
        fecha=date(2026, 11, 3),
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add_all([meta_usd_d, tx_ag_d])

    # Usuario M
    m_user = Usuario(
        id=uuid4(),
        email="usuario_m@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(m_user)

    b_ars_m = Billetera(id=uuid4(), usuario_id=m_user.id, nombre="Caja ARS", moneda=Moneda.ARS, saldo_actual=Decimal("100000.00"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    db.add(b_ars_m)
    db.flush()

    tarjeta_m = TarjetaCredito(
        id=uuid4(),
        usuario_id=m_user.id,
        billetera_id=b_ars_m.id,
        nombre="Visa Galicia",
        red=RedTarjeta.VISA,
        dia_cierre=1,
        dia_vencimiento=10,
        regla_cierre="dia_fijo",
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(tarjeta_m)
    db.flush()

    tx_m1 = Transaccion(id=uuid4(), usuario_id=m_user.id, billetera_id=b_ars_m.id, tarjeta_id=tarjeta_m.id, tipo=TipoTransaccion.EGRESO, monto=Decimal("30000"), moneda=Moneda.ARS, fecha=date(2026, 10, 10), metodo_pago=MetodoPago.TRANSFERENCIA, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA, fecha_creacion=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc))
    tx_m2 = Transaccion(id=uuid4(), usuario_id=m_user.id, billetera_id=b_ars_m.id, tarjeta_id=tarjeta_m.id, tipo=TipoTransaccion.EGRESO, monto=Decimal("50"), moneda=Moneda.USD, fecha=date(2026, 10, 10), metodo_pago=MetodoPago.TRANSFERENCIA, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA, fecha_creacion=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc))
    db.add_all([tx_m1, tx_m2])
    db.flush()

    s_ars_m = SaldoArrastradoTarjeta(id=uuid4(), tarjeta_id=tarjeta_m.id, fecha_vencimiento_resumen=date(2026, 10, 10), monto_inicial=Decimal("30000"), monto_restante=Decimal("30000"), moneda=Moneda.ARS, estado=EstadoSaldoArrastrado.ACTIVO, transaccion_origen_id=tx_m1.id)
    s_usd_m = SaldoArrastradoTarjeta(id=uuid4(), tarjeta_id=tarjeta_m.id, fecha_vencimiento_resumen=date(2026, 10, 10), monto_inicial=Decimal("50"), monto_restante=Decimal("50"), moneda=Moneda.USD, estado=EstadoSaldoArrastrado.ACTIVO, transaccion_origen_id=tx_m2.id)
    db.add_all([s_ars_m, s_usd_m])
    db.commit()

    # Verificar Usuario D
    extra_d = recolectar_extra(db, d_user, hoy, cerrados=[], ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)))
    c9_d = evaluar_extra(extra_d.ingresos_7, extra_d.ingreso_habitual, extra_d.metas, hoy)
    assert c9_d is not None
    assert (
        c9_d.texto
        == "Te entraron US$500 de aguinaldo. Si apartás US$100 ahora para Fondo Retiro, no se diluye en el mes."
    )

    # Verificar Usuario M: dos consejos de arrastre
    extra_m = recolectar_extra(db, m_user, hoy, cerrados=[], ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)))
    c12_m = evaluar_arrastre(extra_m.saldos)
    assert len(c12_m) == 2
    assert {c.moneda for c in c12_m} == {"ARS", "USD"}


def test_aislamiento_y_bloqueo_red_commit_flush(db: Session, monkeypatch):
    """Otro usuario no ve los datos de P; recolectar de P con commit y flush = 0 y red bloqueada."""
    hoy = date(2026, 11, 5)
    _mock_frescura_cobertura(monkeypatch, hoy)

    # Usuario P
    p = Usuario(id=uuid4(), email="p_aislado@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    # Usuario X
    x = Usuario(id=uuid4(), email="x_aislado@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    db.add_all([p, x])

    b_p = Billetera(id=uuid4(), usuario_id=p.id, nombre="Pesos P", moneda=Moneda.ARS, saldo_actual=Decimal("100000"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    db.add(b_p)
    db.flush()

    tx_p = Transaccion(id=uuid4(), usuario_id=p.id, billetera_id=b_p.id, tipo=TipoTransaccion.EGRESO, monto=Decimal("10000"), moneda=Moneda.ARS, fecha=date(2026, 10, 10), metodo_pago=MetodoPago.TRANSFERENCIA, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA, fecha_creacion=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc))
    db.add(tx_p)
    db.commit()

    # Recolectar de X no ve transacciones de P
    datos_x = recolectar(db, x, hoy)
    assert len(datos_x.filas) == 0

    # Tracking commit, flush y bloqueo de red
    commit_count = 0
    flush_count = 0
    orig_commit = db.commit
    orig_flush = db.flush

    def tracked_commit():
        nonlocal commit_count
        commit_count += 1
        return orig_commit()

    def tracked_flush():
        nonlocal flush_count
        flush_count += 1
        return orig_flush()

    monkeypatch.setattr(db, "commit", tracked_commit)
    monkeypatch.setattr(db, "flush", tracked_flush)

    def fail_network(*args, **kwargs):
        raise RuntimeError("LLAMADA DE RED PROHIBIDA")

    with patch("socket.socket", fail_network), patch("urllib.request.urlopen", fail_network):
        _ = recolectar(db, p, hoy)

    assert commit_count == 0
    assert flush_count == 0


def test_medicion_pendientes_arrastre_meta_extra_carga(db: Session, monkeypatch):
    """
    Medición de consejos:
    - arrastre de P emitido 05/11/2026 (saldo 120.000): con hoy 01/02/2027 y saldo SALDADO -> mejoro; sin pagar -> igual
    - meta de P emitida 05/11/2026 (ritmo 50.000) con aportes 100.000 el 10/12 y 10/01 -> mejoro (despues 100.000)
    - extra de P emitido 05/11/2026 con aporte 08/11 -> mejoro (despues 1.00)
    - carga -> sin_dato
    """
    hoy = date(2026, 11, 5)
    _mock_frescura_cobertura(monkeypatch, hoy)

    u = Usuario(id=uuid4(), email="medicion_p@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    db.add(u)

    b_ars = Billetera(id=uuid4(), usuario_id=u.id, nombre="Cuenta ARS", moneda=Moneda.ARS, saldo_actual=Decimal("100000"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    tarjeta = TarjetaCredito(id=uuid4(), usuario_id=u.id, billetera_id=b_ars.id, nombre="Visa", red=RedTarjeta.VISA, dia_cierre=1, dia_vencimiento=10, regla_cierre="dia_fijo", moneda=Moneda.ARS, estado=EstadoTarjeta.ACTIVA)
    db.add_all([b_ars, tarjeta])
    db.flush()

    tx_orig = Transaccion(id=uuid4(), usuario_id=u.id, billetera_id=b_ars.id, tarjeta_id=tarjeta.id, tipo=TipoTransaccion.EGRESO, monto=Decimal("120000"), moneda=Moneda.ARS, fecha=date(2026, 10, 10), metodo_pago=MetodoPago.TRANSFERENCIA, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA, fecha_creacion=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc))
    db.add(tx_orig)
    db.flush()

    saldo_arr = SaldoArrastradoTarjeta(id=uuid4(), tarjeta_id=tarjeta.id, fecha_vencimiento_resumen=date(2026, 10, 10), monto_inicial=Decimal("120000"), monto_restante=Decimal("120000"), moneda=Moneda.ARS, estado=EstadoSaldoArrastrado.ACTIVO, transaccion_origen_id=tx_orig.id)
    db.add(saldo_arr)

    meta = Meta(id=uuid4(), usuario_id=u.id, nombre="Viaje", monto_objetivo=Decimal("1000000"), monto_actual=Decimal("400000"), moneda=Moneda.ARS, fecha_limite=date(2027, 3, 31), estado=EstadoMeta.ACTIVA, fecha_creacion=datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc))
    db.add(meta)
    db.flush()

    # Consejo arrastre emitido 05/11/2026
    c_arrastre = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u.id,
        tipo="arrastre",
        clave=f"arrastre/{tarjeta.id}/ARS/2026-10-10",
        moneda="ARS",
        texto="Texto arrastre",
        valores={"saldo": "120000.00", "tarjeta_id": str(tarjeta.id), "moneda": "ARS"},
        fecha_emision=date(2026, 11, 5),
        fecha_vence=date(2026, 11, 12),
    )
    # Consejo meta emitido 05/11/2026
    c_meta = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u.id,
        tipo="meta",
        clave=f"meta/{meta.id}/2026-11",
        moneda="ARS",
        texto="Texto meta",
        accion="aportar_meta",
        valores={"ritmo": "50000.00", "necesario": "150000.00"},
        fecha_emision=date(2026, 11, 5),
        fecha_vence=date(2026, 11, 12),
    )
    # Consejo extra emitido 05/11/2026
    c_extra = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u.id,
        tipo="extra",
        clave="extra/ing-1",
        moneda="ARS",
        texto="Texto extra",
        valores={"monto": "500000.00", "sugerido": "100000.00"},
        fecha_emision=date(2026, 11, 5),
        fecha_vence=date(2026, 11, 12),
    )
    # Consejo carga emitido 05/11/2026
    c_carga = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u.id,
        tipo="carga",
        clave="carga/ARS/2026-12-01",
        moneda="ARS",
        texto="Texto carga",
        valores={"proxima": "150000.00", "mediana": "100000.00", "libera": "30000.00"},
        fecha_emision=date(2026, 11, 5),
        fecha_vence=date(2026, 11, 12),
    )
    db.add_all([c_arrastre, c_meta, c_extra, c_carga])
    db.commit()

    # Fecha de medición: 01/02/2027 (pasaron c1: dic-2026 y c2: ene-2027)
    hoy_medicion = date(2027, 2, 1)

    # Medición arrastre sin pagar -> despues 120000.00 -> igual
    medir_pendientes(db, u, hoy=hoy_medicion)
    db.refresh(c_arrastre)
    assert c_arrastre.medicion == {"resultado": "igual", "antes": "120000.00", "despues": "120000.00"}

    # Pasar arrastre a SALDADO y resetear mediciones para evaluar c_meta, c_extra y c_carga
    c_arrastre.medicion = None
    c_meta.medicion = None
    c_extra.medicion = None
    c_carga.medicion = None
    saldo_arr.estado = EstadoSaldoArrastrado.SALDADO
    saldo_arr.monto_restante = Decimal("0")
    db.commit()

    # Para meta: agregar aportes de 100.000 el 10/12 y el 10/01
    mov_c1 = MovimientoMeta(id=uuid4(), meta_id=meta.id, billetera_id=b_ars.id, tipo=TipoMovimientoMeta.APORTE, monto=Decimal("100000.00"), moneda_movimiento=Moneda.ARS, fecha=date(2026, 12, 10), fecha_creacion=datetime(2026, 12, 10, 10, 0, 0, tzinfo=timezone.utc))
    mov_c2 = MovimientoMeta(id=uuid4(), meta_id=meta.id, billetera_id=b_ars.id, tipo=TipoMovimientoMeta.APORTE, monto=Decimal("100000.00"), moneda_movimiento=Moneda.ARS, fecha=date(2027, 1, 10), fecha_creacion=datetime(2027, 1, 10, 10, 0, 0, tzinfo=timezone.utc))
    db.add_all([mov_c1, mov_c2])

    # Para extra: agregar un aporte el 08/11/2026 (dentro de los 7 días de fecha_emision)
    mov_ext = MovimientoMeta(id=uuid4(), meta_id=meta.id, billetera_id=b_ars.id, tipo=TipoMovimientoMeta.APORTE, monto=Decimal("20000.00"), moneda_movimiento=Moneda.ARS, fecha=date(2026, 11, 8), fecha_creacion=datetime(2026, 11, 8, 10, 0, 0, tzinfo=timezone.utc))
    db.add(mov_ext)
    db.commit()

    medir_pendientes(db, u, hoy=hoy_medicion)
    db.refresh(c_arrastre)
    db.refresh(c_meta)
    db.refresh(c_extra)
    db.refresh(c_carga)

    assert c_arrastre.medicion == {"resultado": "mejoro", "antes": "120000.00", "despues": "0.00"}
    assert c_meta.medicion == {"resultado": "mejoro", "antes": "50000.00", "despues": "100000.00"}
    assert c_extra.medicion == {"resultado": "mejoro", "antes": "0.00", "despues": "1.00"}
    assert c_carga.medicion == {"resultado": "sin_dato", "antes": "0.00", "despues": "0.00"}


def test_get_consejo_actual_accion_texto(db: Session, monkeypatch):
    """GET /consejos/actual con consejo de accion 'aportar_meta' -> accion_texto 'Aportar a meta'."""
    hoy = date(2026, 11, 5)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy)

    u = Usuario(id=uuid4(), email="test_api@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    db.add(u)
    db.flush()

    c = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u.id,
        tipo="meta",
        clave="meta/id1/2026-11",
        moneda="ARS",
        texto="Texto meta",
        accion="aportar_meta",
        valores={},
        fecha_emision=hoy,
        fecha_vence=hoy + timedelta(days=7),
    )
    db.add(c)
    db.commit()

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: db

    try:
        res = client.get("/consejos/actual")
        assert res.status_code == 200
        data = res.json()
        assert data["consejo"] is not None
        assert data["consejo"]["accion"] == "aportar_meta"
        assert data["consejo"]["accion_texto"] == "Aportar a meta"
    finally:
        app.dependency_overrides.clear()


def test_consejo_cumplido_todos_los_casos(db: Session, monkeypatch):
    """
    Pruebas exhaustivas de consejo_cumplido (E-G), hoy 05/11/2026, emitidos 05/11, vencen 12/11:
    (1) sin_presupuesto de Indumentaria
    (2) colchon
    (3) extra
    (4) meta (aportar_meta y poner_fecha_meta)
    (5) ritmo
    (6) arrastre
    (7) sin_ingresos
    (8) plata_quieta
    (9) hormiga
    (10) Aislamiento de otro usuario
    (11) POST /consejos/{id}/respuesta con 'resuelto' -> 422
    (12) resolver_cumplidos no toca vencidos ni con respuesta
    """
    hoy = date(2026, 11, 5)
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.consejos_service.hoy_argentina", lambda: hoy)

    u = Usuario(id=uuid4(), email="cumplido_u@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    otro_u = Usuario(id=uuid4(), email="cumplido_otro@argentum.com", auth_provider=AuthProvider.EMAIL, rol=RolUsuario.USUARIO, estado=EstadoUsuario.ACTIVO, moneda_principal=Moneda.ARS, ciclo_tipo=CicloTipo.DIA_FIJO, ciclo_valor="1", onboarding_completo=True)
    db.add_all([u, otro_u])

    b_ars = Billetera(id=uuid4(), usuario_id=u.id, nombre="Billetera ARS", moneda=Moneda.ARS, saldo_actual=Decimal("100000"), es_inversion=False, estado=EstadoBilletera.ACTIVA)
    tarjeta = TarjetaCredito(id=uuid4(), usuario_id=u.id, billetera_id=b_ars.id, nombre="Visa", red=RedTarjeta.VISA, dia_cierre=1, dia_vencimiento=10, regla_cierre="dia_fijo", moneda=Moneda.ARS, estado=EstadoTarjeta.ACTIVA)
    cat_ind = Categoria(id=uuid4(), nombre="Indumentaria", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    sub_ropa = Subcategoria(id=uuid4(), categoria_id=cat_ind.id, nombre="Ropa", estado=EstadoSubcategoria.ACTIVA)
    cat_ocio = Categoria(id=uuid4(), nombre="Ocio", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
    db.add_all([b_ars, tarjeta, cat_ind, sub_ropa, cat_ocio])
    db.flush()

    # (1) sin_presupuesto de Indumentaria
    c_sp = ConsejoEmitido(
        id=uuid4(),
        usuario_id=u.id,
        tipo="sin_presupuesto",
        clave="sin_presupuesto/Indumentaria/2026-10",
        moneda="ARS",
        texto="Sugerencia presupuesto Indumentaria",
        accion="armar_presupuesto",
        valores={"categoria_id": str(cat_ind.id), "categoria": "Indumentaria"},
        fecha_emision=hoy,
        fecha_vence=date(2026, 11, 12),
    )
    db.add(c_sp)
    db.commit()

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: u
    app.dependency_overrides[get_db] = lambda: db

    try:
        # GET /consejos/actual -> lo devuelve
        r1 = client.get("/consejos/actual")
        assert r1.status_code == 200
        assert r1.json()["consejo"] is not None

        # Presupuesto de Ocio no lo resuelve
        presu_ocio = Presupuesto(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Presupuesto Ocio",
            monto=Decimal("50000"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            estado=EstadoPresupuesto.ACTIVO,
            fecha_creacion=datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc),
        )
        db.add(presu_ocio)
        db.add(PresupuestoCategoria(presupuesto_id=presu_ocio.id, categoria_id=cat_ocio.id))
        db.commit()

        assert consejo_cumplido(db, u, c_sp, hoy) is False

        # Presupuesto PAUSADO de Indumentaria > Ropa -> lo resuelve
        presu_pausado = Presupuesto(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Presupuesto Ropa",
            monto=Decimal("50000"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            estado=EstadoPresupuesto.PAUSADO,
            fecha_creacion=datetime(2026, 11, 5, 13, 0, 0, tzinfo=timezone.utc),
        )
        db.add(presu_pausado)
        db.add(PresupuestoCategoria(presupuesto_id=presu_pausado.id, subcategoria_id=sub_ropa.id))
        db.commit()

        assert consejo_cumplido(db, u, c_sp, hoy) is True

        # GET /consejos/actual -> resuelve y devuelve {"consejo": null}
        r2 = client.get("/consejos/actual")
        assert r2.status_code == 200
        assert r2.json()["consejo"] is None
        db.refresh(c_sp)
        assert c_sp.respuesta == "resuelto"

        # (10) Otro usuario que arma presupuesto de Tecnología no resuelve consejo de P
        cat_tec = Categoria(id=uuid4(), nombre="Tecnología", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
        db.add(cat_tec)
        db.flush()

        c_sp_otro = ConsejoEmitido(
            id=uuid4(),
            usuario_id=u.id,
            tipo="sin_presupuesto",
            clave="sin_presupuesto/Tecnologia/otra",
            texto="Texto sin presupuesto otro",
            valores={"categoria_id": str(cat_tec.id)},
            fecha_emision=hoy,
            fecha_vence=date(2026, 11, 12),
        )
        db.add(c_sp_otro)
        db.commit()

        presu_otro_usr = Presupuesto(
            id=uuid4(),
            usuario_id=otro_u.id,
            nombre="Presupuesto Tecnología Otro",
            monto=Decimal("50000"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            estado=EstadoPresupuesto.ACTIVO,
            fecha_creacion=datetime(2026, 11, 5, 14, 0, 0, tzinfo=timezone.utc),
        )
        db.add(presu_otro_usr)
        db.add(PresupuestoCategoria(presupuesto_id=presu_otro_usr.id, categoria_id=cat_tec.id))
        db.commit()

        assert consejo_cumplido(db, u, c_sp_otro, hoy) is False

        # Al otro día emitir_si_corresponde no vuelve a emitir un sin_presupuesto de Indumentaria
        # porque la categoría ya está cubierta en categorias_con_presupuesto
        cands_06 = emitir_si_corresponde(db, u, hoy=date(2026, 11, 6), inflacion=None, commit=False)
        assert cands_06 is None or cands_06.tipo != "sin_presupuesto"

        # (2) Colchón: meta creada 01/10 "Auto" -> sigue
        c_colchon = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="colchon", clave="colchon/ARS/2026-11-05", texto="Texto colchon", valores={}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        meta_auto_vieja = Meta(id=uuid4(), usuario_id=u.id, nombre="Auto", monto_objetivo=Decimal("100000"), moneda=Moneda.ARS, estado=EstadoMeta.ACTIVA, fecha_creacion=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc))
        db.add_all([c_colchon, meta_auto_vieja])
        db.commit()
        assert consejo_cumplido(db, u, c_colchon, hoy) is False

        # (3) Extra: aporte del 05/11 a cualquier meta -> resuelto; aporte del 04/11 -> sigue
        c_extra_cumpl = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="extra", clave="extra/ing-x", texto="Texto extra", valores={}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        db.add(c_extra_cumpl)
        db.commit()
        assert consejo_cumplido(db, u, c_extra_cumpl, hoy) is False

        mov_04 = MovimientoMeta(id=uuid4(), meta_id=meta_auto_vieja.id, billetera_id=b_ars.id, tipo=TipoMovimientoMeta.APORTE, monto=Decimal("1000"), moneda_movimiento=Moneda.ARS, fecha=date(2026, 11, 4), fecha_creacion=datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc))
        db.add(mov_04)
        db.commit()
        assert consejo_cumplido(db, u, c_extra_cumpl, hoy) is False

        mov_05 = MovimientoMeta(id=uuid4(), meta_id=meta_auto_vieja.id, billetera_id=b_ars.id, tipo=TipoMovimientoMeta.APORTE, monto=Decimal("1000"), moneda_movimiento=Moneda.ARS, fecha=date(2026, 11, 5), fecha_creacion=datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc))
        db.add(mov_05)
        db.commit()
        assert consejo_cumplido(db, u, c_extra_cumpl, hoy) is True

        # Crear meta el 05/11 llamada "Vacaciones" -> resuelve colchón
        meta_vac_nueva = Meta(id=uuid4(), usuario_id=u.id, nombre="Vacaciones", monto_objetivo=Decimal("100000"), moneda=Moneda.ARS, estado=EstadoMeta.ACTIVA, fecha_creacion=datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc))
        db.add(meta_vac_nueva)
        db.commit()
        assert consejo_cumplido(db, u, c_colchon, hoy) is True

        # (4) Meta aportar_meta y poner_fecha_meta
        meta_viaje_t = Meta(id=uuid4(), usuario_id=u.id, nombre="Viaje Test", monto_objetivo=Decimal("100000"), moneda=Moneda.ARS, estado=EstadoMeta.ACTIVA, fecha_creacion=datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc))
        meta_auto_t = Meta(id=uuid4(), usuario_id=u.id, nombre="Auto Test", monto_objetivo=Decimal("100000"), moneda=Moneda.ARS, estado=EstadoMeta.ACTIVA, fecha_creacion=datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc))
        db.add_all([meta_viaje_t, meta_auto_t])
        db.commit()

        c_meta_ap = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="meta", clave=f"meta/{meta_viaje_t.id}/2026-11", texto="Texto aportar", accion="aportar_meta", valores={}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        c_meta_pf = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="meta", clave=f"meta/{meta_auto_t.id}/sin_fecha", texto="Texto fecha", accion="poner_fecha_meta", valores={}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        db.add_all([c_meta_ap, c_meta_pf])
        db.commit()

        # Aporte a otra meta -> sigue
        mov_otro = MovimientoMeta(id=uuid4(), meta_id=meta_auto_t.id, billetera_id=b_ars.id, tipo=TipoMovimientoMeta.APORTE, monto=Decimal("5000"), moneda_movimiento=Moneda.ARS, fecha=date(2026, 11, 6), fecha_creacion=datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc))
        db.add(mov_otro)
        db.commit()
        assert consejo_cumplido(db, u, c_meta_ap, date(2026, 11, 6)) is False

        # Aporte a Viaje Test el 06/11 -> resuelto
        mov_viaje = MovimientoMeta(id=uuid4(), meta_id=meta_viaje_t.id, billetera_id=b_ars.id, tipo=TipoMovimientoMeta.APORTE, monto=Decimal("5000"), moneda_movimiento=Moneda.ARS, fecha=date(2026, 11, 6), fecha_creacion=datetime(2026, 11, 6, 10, 0, 0, tzinfo=timezone.utc))
        db.add(mov_viaje)
        db.commit()
        assert consejo_cumplido(db, u, c_meta_ap, date(2026, 11, 6)) is True

        # Ponerle fecha a Auto Test -> resuelto
        assert consejo_cumplido(db, u, c_meta_pf, hoy) is False
        meta_auto_t.fecha_limite = date(2027, 5, 1)
        db.commit()
        assert consejo_cumplido(db, u, c_meta_pf, hoy) is True

        # (5) Ritmo de "Casa" (limite 400000.00)
        presu_casa = Presupuesto(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Casa",
            monto=Decimal("400000"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            estado=EstadoPresupuesto.ACTIVO,
            fecha_creacion=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        db.add(presu_casa)
        db.flush()
        per_casa = PeriodoPresupuesto(id=uuid4(), presupuesto_id=presu_casa.id, fecha_inicio=date(2026, 11, 1), fecha_fin=date(2026, 11, 30), monto_limite=Decimal("400000"))
        db.add(per_casa)
        c_ritmo = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="ritmo", clave=f"ritmo/{presu_casa.id}/2026-11-01", texto="Texto ritmo", valores={"presupuesto_id": str(presu_casa.id), "inicio": "2026-11-01", "limite": "400000.00"}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        db.add(c_ritmo)
        db.commit()

        assert consejo_cumplido(db, u, c_ritmo, hoy) is False
        # Subir límite a 450.000 -> resuelto
        per_casa.monto_limite = Decimal("450000")
        db.commit()
        assert consejo_cumplido(db, u, c_ritmo, hoy) is True
        # Presupuesto pausado -> resuelto
        per_casa.monto_limite = Decimal("400000")
        presu_casa.estado = EstadoPresupuesto.PAUSADO
        db.commit()
        assert consejo_cumplido(db, u, c_ritmo, hoy) is True

        # (6) Arrastre de Visa ARS: pagado a medias -> sigue; saldo SALDADO -> resuelto
        tx_arr_t = Transaccion(id=uuid4(), usuario_id=u.id, billetera_id=b_ars.id, tarjeta_id=tarjeta.id, tipo=TipoTransaccion.EGRESO, monto=Decimal("100000"), moneda=Moneda.ARS, fecha=date(2026, 10, 10), metodo_pago=MetodoPago.TRANSFERENCIA, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA, fecha_creacion=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc))
        db.add(tx_arr_t)
        db.flush()
        s_visa_t = SaldoArrastradoTarjeta(id=uuid4(), tarjeta_id=tarjeta.id, fecha_vencimiento_resumen=date(2026, 10, 10), monto_inicial=Decimal("100000"), monto_restante=Decimal("60000"), moneda=Moneda.ARS, estado=EstadoSaldoArrastrado.ACTIVO, transaccion_origen_id=tx_arr_t.id)
        db.add(s_visa_t)
        c_arr_t = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="arrastre", clave=f"arrastre/{tarjeta.id}/ARS/2026-10-10", texto="Texto arrastre", valores={"tarjeta_id": str(tarjeta.id), "moneda": "ARS", "saldo": "100000.00"}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        db.add(c_arr_t)
        db.commit()

        # Pagado a medias (monto_restante 60.000) -> sigue
        assert consejo_cumplido(db, u, c_arr_t, hoy) is False
        # SALDADO -> resuelto
        s_visa_t.estado = EstadoSaldoArrastrado.SALDADO
        s_visa_t.monto_restante = Decimal("0")
        db.commit()
        assert consejo_cumplido(db, u, c_arr_t, hoy) is True

        # (7) Sin ingresos: con un ingreso del 05/11 -> resuelto; sin ingresos -> sigue
        c_sin_ing = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="sin_ingresos", clave="sin_ingresos/2026-11-05", texto="Texto sin ingresos", valores={}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        db.add(c_sin_ing)
        db.commit()
        assert consejo_cumplido(db, u, c_sin_ing, hoy) is False

        tx_ing_05 = Transaccion(id=uuid4(), usuario_id=u.id, billetera_id=b_ars.id, tipo=TipoTransaccion.INGRESO, monto=Decimal("50000"), moneda=Moneda.ARS, fecha=hoy, metodo_pago=MetodoPago.TRANSFERENCIA, origen=OrigenTransaccion.MANUAL, estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA, fecha_creacion=datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc))
        db.add(tx_ing_05)
        db.commit()
        assert consejo_cumplido(db, u, c_sin_ing, hoy) is True

        # (8) Plata quieta (inversiones 0.00): con billetera ARS de inversión de 100.000 -> resuelto
        c_pq = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="plata_quieta", clave="plata_quieta/2026-11-05", texto="Texto plata quieta", valores={"inversiones": "0.00"}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        db.add(c_pq)
        db.commit()
        assert consejo_cumplido(db, u, c_pq, hoy) is False

        b_inv_ars = Billetera(id=uuid4(), usuario_id=u.id, nombre="Inversión ARS", moneda=Moneda.ARS, saldo_actual=Decimal("100000"), es_inversion=True, estado=EstadoBilletera.ACTIVA)
        db.add(b_inv_ars)
        db.commit()
        assert consejo_cumplido(db, u, c_pq, hoy) is True

        # (9) Hormiga -> nunca se resuelve sola
        c_hormiga = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="hormiga", clave="hormiga/ARS/2026-10-01", texto="Texto hormiga", valores={}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12))
        db.add(c_hormiga)
        db.commit()
        assert consejo_cumplido(db, u, c_hormiga, hoy) is False

        # (11) POST /consejos/{id}/respuesta con 'resuelto' -> 422
        resp_422 = client.post(f"/consejos/{c_hormiga.id}/respuesta", json={"respuesta": "resuelto"})
        assert resp_422.status_code == 422

        # (12) resolver_cumplidos no toca consejos vencidos ni con respuesta
        c_vencido = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="plata_quieta", clave="pq_vencido", texto="Texto vencido", valores={"inversiones": "0.00"}, fecha_emision=date(2026, 10, 1), fecha_vence=date(2026, 10, 8), respuesta=None)
        c_con_resp = ConsejoEmitido(id=uuid4(), usuario_id=u.id, tipo="plata_quieta", clave="pq_con_resp", texto="Texto con respuesta", valores={"inversiones": "0.00"}, fecha_emision=hoy, fecha_vence=date(2026, 11, 12), respuesta="sirvio")
        db.add_all([c_vencido, c_con_resp])
        db.commit()

        cant_res = resolver_cumplidos(db, u, hoy)
        assert cant_res >= 1
        db.refresh(c_vencido)
        db.refresh(c_con_resp)
        assert c_vencido.respuesta is None
        assert c_con_resp.respuesta == "sirvio"

    finally:
        app.dependency_overrides.clear()


def test_t2_ingreso_habitual_normalizado_upper(db: Session, monkeypatch):
    """
    T-2: Con obtener_ingreso_habitual devolviendo clave en minúscula 'ars'
    recolectar_extra normaliza a 'ARS' y C9 reconoce 'más de lo habitual'.
    """
    from dataclasses import dataclass

    @dataclass
    class _MockHab:
        monto: Decimal = Decimal("1000000")

    monkeypatch.setattr(
        "app.services.consejos_extra_datos.obtener_ingreso_habitual",
        lambda *args, **kwargs: {"ars": _MockHab()},
    )
    hoy = date(2026, 11, 5)
    _mock_frescura_cobertura(monkeypatch, hoy)

    u = Usuario(
        id=uuid4(),
        email="test_t2_normalizado@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(u)
    b_ars = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("2000000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b_ars)

    cat_trabajo = Categoria(id=uuid4(), nombre="Trabajo", tipo=TipoCategoria.INGRESO, estado=EstadoCategoria.ACTIVA)
    sub_sueldo = Subcategoria(id=uuid4(), categoria_id=cat_trabajo.id, nombre="Sueldo", estado=EstadoSubcategoria.ACTIVA)
    db.add_all([cat_trabajo, sub_sueldo])
    db.flush()

    tx_sueldo = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        categoria_id=cat_trabajo.id,
        subcategoria_id=sub_sueldo.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("1600000"),
        moneda=Moneda.ARS,
        fecha=hoy,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc),
    )
    meta_viaje = Meta(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Viaje",
        moneda=Moneda.ARS,
        monto_objetivo=Decimal("10000000"),
        monto_actual=Decimal("1000000"),
        fecha_limite=date(2027, 12, 31),
        estado=EstadoMeta.ACTIVA,
        fecha_creacion=datetime(2026, 8, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    db.add_all([tx_sueldo, meta_viaje])
    db.commit()

    extra = recolectar_extra(db, u, hoy, cerrados=[], ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)))
    assert extra.ingreso_habitual == {"ARS": Decimal("1000000")}

    datos = recolectar(db, u, hoy)
    candidatos = evaluar_candidatos(datos)
    c9_match = [c for c in candidatos if c.tipo == "extra"]
    assert len(c9_match) == 1
    assert (
        c9_match[0].texto
        == "Te entraron $1.600.000, más de lo habitual. Si apartás $320.000 ahora para Viaje, no se diluye en el mes."
    )


