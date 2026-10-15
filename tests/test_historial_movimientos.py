"""
Pruebas integrales de historial unificado de movimientos financieros (fase5b_historial).
Ubicación: tests/test_historial_movimientos.py
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.services import dashboard_service, historial_service
from app.services.definiciones_service import (
    ContextoDefiniciones,
    etiqueta_movimiento,
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
                    value = UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    sqlite3.register_adapter(UUID, lambda u: u.hex)
    yield


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
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
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def fixtures_historial(db_session: Session):
    """
    Datos fijados por la especificación:
    - Usuario con ciclo 01/10 al 31/10 (ciclo día 1 fijo).
    - Galicia (ARS 150.000) y Mercado Pago (ARS 0) creadas 2026-09-30 12:00 Argentina.
    - Tarjeta Visa vinculada a Galicia.
    - 2026-10-02 débito Galicia "Súper" 10.000, categoría Supermercado.
    - 2026-10-03 compra tarjeta "Farmacia" en 1 pago, 30.000 (padre + hija 2026-11-04).
    - Compra tarjeta "Zapatillas" 2026-09-20 en 3 cuotas de 20.000 (padre + hijas 2026-10-07, 2026-11-04 y 2026-12-04).
    - 2026-10-07 egreso Galicia de 50.000 con pago_resumen_vencimiento 2026-10-07.
    - 2026-10-08 transferencia Galicia → Mercado Pago de 40.000.
    - 2026-10-09 aporte a meta desde Galicia de 5.000 (con su movimiento_meta).
    - 2026-10-10 rendimiento en Mercado Pago de 800.
    - 2026-10-11 ajuste en Galicia de -1.000.
    - 2026-10-12 ingreso Galicia "Sueldo" 200.000.
    - 2026-10-13 egreso Galicia "Kiosco" 2.000 en estado pendiente.
    - 2026-10-20 egreso Galicia "Gimnasio" 15.000.
    """
    usuario = Usuario(
        id=uuid4(),
        email="test_historial@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Usuario",
        apellido="Historial",
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db_session.add(usuario)
    db_session.flush()

    # Fecha creación 2026-09-30 a las 12:00 de Argentina (UTC-3 -> 15:00 UTC)
    fc_billeteras = datetime(2026, 9, 30, 15, 0, 0, tzinfo=timezone.utc)

    b_galicia = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("150000.00"),
        saldo_actual=Decimal("150000.00"),
        es_efectivo=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=fc_billeteras,
    )
    b_mp = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        es_efectivo=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=fc_billeteras,
    )
    db_session.add_all([b_galicia, b_mp])
    db_session.flush()

    tarjeta_visa = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        nombre="Visa Galicia",
        limite_credito=Decimal("1000000.00"),
        dia_cierre=20,
        dia_vencimiento=4,
        red=RedTarjeta.VISA,
        estado=EstadoTarjeta.ACTIVA,
        moneda=Moneda.ARS,
    )
    db_session.add(tarjeta_visa)

    cat_super = Categoria(
        id=uuid4(),
        nombre="Supermercado",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_general = Categoria(
        id=uuid4(),
        nombre="General",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db_session.add_all([cat_super, cat_general])
    db_session.flush()

    # 1. 2026-10-02 débito Galicia "Súper" 10.000, categoría Supermercado
    tx_super = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        categoria_id=cat_super.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 2),
        descripcion="Súper",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_super)

    # 2. 2026-10-03 compra tarjeta "Farmacia" en 1 pago, 30.000 (padre + hija con fecha 2026-11-04)
    id_padre_farm = uuid4()
    id_grupo_farm = uuid4()

    padre_farm = Transaccion(
        id=id_padre_farm,
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tarjeta_id=tarjeta_visa.id,
        grupo_cuotas_id=id_grupo_farm,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 3),
        descripcion="Farmacia",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(padre_farm)
    db_session.flush()

    grupo_farm = GrupoCuotas(
        id=id_grupo_farm,
        usuario_id=usuario.id,
        tarjeta_id=tarjeta_visa.id,
        transaccion_padre_id=id_padre_farm,
        descripcion="Farmacia",
        cantidad_cuotas=1,
        monto_total=Decimal("30000.00"),
        total_financiado=Decimal("30000.00"),
        moneda=Moneda.ARS,
    )
    db_session.add(grupo_farm)
    db_session.flush()

    hija_farm = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tarjeta_id=tarjeta_visa.id,
        grupo_cuotas_id=grupo_farm.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 4),
        descripcion="Farmacia",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 3, 12, 1, 0, tzinfo=timezone.utc),
    )
    db_session.add(hija_farm)
    db_session.flush()

    cuota_farm = Cuota(
        id=uuid4(),
        grupo_id=grupo_farm.id,
        transaccion_id=hija_farm.id,
        numero_cuota=1,
        monto_proyectado=Decimal("30000.00"),
        fecha_vencimiento=date(2026, 11, 4),
    )
    db_session.add(cuota_farm)

    # 3. Compra tarjeta "Zapatillas" del 2026-09-20 en 3 cuotas de 20.000 (padre + hijas 2026-10-07, 2026-11-04 y 2026-12-04)
    id_padre_zap = uuid4()
    id_grupo_zap = uuid4()

    padre_zap = Transaccion(
        id=id_padre_zap,
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tarjeta_id=tarjeta_visa.id,
        grupo_cuotas_id=id_grupo_zap,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("60000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 20),
        descripcion="Zapatillas",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 9, 20, 10, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(padre_zap)
    db_session.flush()

    grupo_zap = GrupoCuotas(
        id=id_grupo_zap,
        usuario_id=usuario.id,
        tarjeta_id=tarjeta_visa.id,
        transaccion_padre_id=id_padre_zap,
        descripcion="Zapatillas",
        cantidad_cuotas=3,
        monto_total=Decimal("60000.00"),
        total_financiado=Decimal("60000.00"),
        moneda=Moneda.ARS,
    )
    db_session.add(grupo_zap)
    db_session.flush()
    hija_zap_1 = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tarjeta_id=tarjeta_visa.id,
        grupo_cuotas_id=grupo_zap.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 7),
        descripcion="Zapatillas (Cuota 1/3)",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 9, 20, 10, 1, 0, tzinfo=timezone.utc),
    )
    hija_zap_2 = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tarjeta_id=tarjeta_visa.id,
        grupo_cuotas_id=grupo_zap.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 4),
        descripcion="Zapatillas (Cuota 2/3)",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 9, 20, 10, 2, 0, tzinfo=timezone.utc),
    )
    hija_zap_3 = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tarjeta_id=tarjeta_visa.id,
        grupo_cuotas_id=grupo_zap.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("20000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 12, 4),
        descripcion="Zapatillas (Cuota 3/3)",
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 9, 20, 10, 3, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([padre_zap, hija_zap_1, hija_zap_2, hija_zap_3])
    db_session.flush()

    c_zap_1 = Cuota(
        id=uuid4(),
        grupo_id=grupo_zap.id,
        transaccion_id=hija_zap_1.id,
        numero_cuota=1,
        monto_proyectado=Decimal("20000.00"),
        fecha_vencimiento=date(2026, 10, 7),
    )
    c_zap_2 = Cuota(
        id=uuid4(),
        grupo_id=grupo_zap.id,
        transaccion_id=hija_zap_2.id,
        numero_cuota=2,
        monto_proyectado=Decimal("20000.00"),
        fecha_vencimiento=date(2026, 11, 4),
    )
    c_zap_3 = Cuota(
        id=uuid4(),
        grupo_id=grupo_zap.id,
        transaccion_id=hija_zap_3.id,
        numero_cuota=3,
        monto_proyectado=Decimal("20000.00"),
        fecha_vencimiento=date(2026, 12, 4),
    )
    db_session.add_all([c_zap_1, c_zap_2, c_zap_3])

    # 4. 2026-10-07 egreso Galicia de 50.000 con pago_resumen_vencimiento 2026-10-07
    tx_pago_res = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 7),
        descripcion="Pago de tarjeta",
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        pago_resumen_vencimiento=date(2026, 10, 7),
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 7, 9, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_pago_res)

    # 5. 2026-10-08 transferencia Galicia → Mercado Pago de 40.000
    tr_interna = TransferenciaInterna(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_origen_id=b_galicia.id,
        billetera_destino_id=b_mp.id,
        monto=Decimal("40000.00"),
        monto_origen=Decimal("40000.00"),
        monto_destino=Decimal("40000.00"),
        moneda=Moneda.ARS,
        moneda_origen=Moneda.ARS,
        moneda_destino=Moneda.ARS,
        fecha=date(2026, 10, 8),
        fecha_creacion=datetime(2026, 10, 8, 11, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(tr_interna)

    # 6. 2026-10-09 aporte a meta desde Galicia de 5.000 (con su movimiento_meta)
    meta = Meta(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Vacaciones",
        monto_objetivo=Decimal("100000.00"),
        moneda=Moneda.ARS,
        monto_actual=Decimal("5000.00"),
        estado=EstadoMeta.ACTIVA,
        fecha_creacion=datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(meta)
    db_session.flush()

    mov_meta = MovimientoMeta(
        id=uuid4(),
        meta_id=meta.id,
        tipo=TipoMovimientoMeta.APORTE,
        monto=Decimal("5000.00"),
        moneda_movimiento=Moneda.ARS,
        billetera_id=b_galicia.id,
        fecha=date(2026, 10, 9),
        fecha_creacion=datetime(2026, 10, 9, 10, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(mov_meta)
    db_session.flush()

    tx_aporte = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("5000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 9),
        descripcion="Aporte a meta",
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        movimiento_meta_id=mov_meta.id,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 9, 10, 1, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_aporte)

    # 7. 2026-10-10 rendimiento en Mercado Pago de 800
    rend = RendimientoBilletera(
        id=uuid4(),
        billetera_id=b_mp.id,
        monto=Decimal("800.00"),
        fecha=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc),
        fecha_creacion=datetime(2026, 10, 10, 10, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(rend)

    # 8. 2026-10-11 ajuste en Galicia de −1.000
    ajuste = AjusteSaldo(
        id=uuid4(),
        billetera_id=b_galicia.id,
        monto=Decimal("-1000.00"),
        saldo_anterior=Decimal("45000.00"),
        saldo_declarado=Decimal("44000.00"),
        fecha=date(2026, 10, 11),
        fecha_creacion=datetime(2026, 10, 11, 14, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(ajuste)

    # 9. 2026-10-12 ingreso Galicia "Sueldo" 200.000
    tx_sueldo = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("200000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 12),
        descripcion="Sueldo",
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_sueldo)

    # 10. 2026-10-13 egreso Galicia "Kiosco" 2.000 en estado pendiente
    tx_kiosco = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 13),
        descripcion="Kiosco",
        metodo_pago=MetodoPago.EFECTIVO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        fecha_creacion=datetime(2026, 10, 13, 16, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_kiosco)

    # 11. 2026-10-20 egreso Galicia "Gimnasio" 15.000
    tx_gym = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("15000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 20),
        descripcion="Gimnasio",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 10, 20, 18, 0, 0, tzinfo=timezone.utc),
    )
    db_session.add(tx_gym)

    db_session.commit()

    return {
        "usuario": usuario,
        "galicia": b_galicia,
        "mp": b_mp,
        "tarjeta_visa": tarjeta_visa,
        "cat_super": cat_super,
        "cat_general": cat_general,
    }


def test_caso_a_modo_general_sin_filtros(db_session: Session, fixtures_historial):
    """
    a) Modo general del 01/10 al 31/10, sin filtros:
       11 ítems con estas descripciones y etiquetas exactas:
       Súper Gasto, Farmacia Gasto, "Zapatillas 1/3" Gasto (cuota_texto "1/3"), Pago de tarjeta,
       "Galicia → Mercado Pago" Transferencia (neutro), Aporte a meta, Rendimiento (entra),
       Ajuste de saldo (sale, monto 1.000), Sueldo Ingreso, Kiosco Por confirmar, Gimnasio Programado.
       Cada aclaración igual al texto exacto de la decisión 4.
       No aparecen el padre de Zapatillas ni la hija de Farmacia.
       Totales ARS: gastos 60000.00, ingresos 200000.00, balance 140000.00; USD en 0.
       Fechas no crecientes, primer ítem Gimnasio.
    """
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        hoy=hoy,
    )

    assert res.total_items == 11
    assert len(res.items) == 11

    # Fechas no crecientes y primer ítem Gimnasio
    assert res.items[0].descripcion == "Gimnasio"
    for i in range(len(res.items) - 1):
        assert res.items[i].fecha >= res.items[i + 1].fecha

    items_map = {item.descripcion: item for item in res.items}

    # Validaciones individuales de descripciones, etiquetas, aclaraciones y sentidos
    assert items_map["Gimnasio"].etiqueta == "Programado"
    assert items_map["Gimnasio"].cuenta_como == "nada"
    assert items_map["Gimnasio"].aclaracion == "Todavía no pasó"

    assert items_map["Kiosco"].etiqueta == "Por confirmar"
    assert items_map["Kiosco"].cuenta_como == "nada"
    assert items_map["Kiosco"].aclaracion == "Falta confirmarlo"

    assert items_map["Sueldo"].etiqueta == "Ingreso"
    assert items_map["Sueldo"].cuenta_como == "ingreso"
    assert items_map["Sueldo"].aclaracion is None

    assert items_map["Ajuste de saldo"].etiqueta == "Ajuste de saldo"
    assert items_map["Ajuste de saldo"].cuenta_como == "nada"
    assert items_map["Ajuste de saldo"].aclaracion == "Corrección del saldo: no es gasto ni ingreso"
    assert items_map["Ajuste de saldo"].sentido == "sale"
    assert items_map["Ajuste de saldo"].monto == Decimal("1000.00")

    assert items_map["Rendimiento"].etiqueta == "Rendimiento"
    assert items_map["Rendimiento"].cuenta_como == "nada"
    assert items_map["Rendimiento"].aclaracion == "No suma a tus ingresos"
    assert items_map["Rendimiento"].sentido == "entra"

    assert items_map["Aporte a meta"].etiqueta == "Aporte a meta"
    assert items_map["Aporte a meta"].cuenta_como == "nada"
    assert items_map["Aporte a meta"].aclaracion == "No es gasto: va a tu meta"

    assert items_map["Galicia → Mercado Pago"].etiqueta == "Transferencia"
    assert items_map["Galicia → Mercado Pago"].cuenta_como == "nada"
    assert items_map["Galicia → Mercado Pago"].aclaracion == "Entre tus cuentas: no es gasto ni ingreso"
    assert items_map["Galicia → Mercado Pago"].sentido == "neutro"

    assert items_map["Pago de tarjeta"].etiqueta == "Pago de tarjeta"
    assert items_map["Pago de tarjeta"].cuenta_como == "nada"
    assert items_map["Pago de tarjeta"].aclaracion == "No es gasto: las compras ya se contaron"

    assert "Zapatillas 1/3" in items_map
    assert items_map["Zapatillas 1/3"].etiqueta == "Gasto"
    assert items_map["Zapatillas 1/3"].cuenta_como == "gasto"
    assert items_map["Zapatillas 1/3"].aclaracion is None
    assert items_map["Zapatillas 1/3"].cuota_texto == "1/3"

    assert items_map["Farmacia"].etiqueta == "Gasto"
    assert items_map["Farmacia"].cuenta_como == "gasto"
    assert items_map["Farmacia"].aclaracion is None

    assert items_map["Súper"].etiqueta == "Gasto"
    assert items_map["Súper"].cuenta_como == "gasto"
    assert items_map["Súper"].aclaracion is None

    # No aparecen el padre de Zapatillas ni la hija de Farmacia
    descs = [it.descripcion for it in res.items]
    assert "Zapatillas" not in descs
    assert "Farmacia 1/1" not in descs

    # Totales ARS y USD
    assert res.totales["ARS"].gastos == Decimal("60000.00")
    assert res.totales["ARS"].ingresos == Decimal("200000.00")
    assert res.totales["ARS"].balance == Decimal("140000.00")
    assert res.totales["USD"].gastos == Decimal("0.00")
    assert res.totales["USD"].ingresos == Decimal("0.00")
    assert res.totales["USD"].balance == Decimal("0.00")


def test_caso_b_paginado(db_session: Session, fixtures_historial):
    """b) Mismo pedido con skip 0 y limit 4: 4 ítems, hay_mas True, total_items 11 y los mismos totales."""
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        skip=0,
        limit=4,
        hoy=hoy,
    )

    assert len(res.items) == 4
    assert res.hay_mas is True
    assert res.total_items == 11
    assert res.totales["ARS"].gastos == Decimal("60000.00")
    assert res.totales["ARS"].ingresos == Decimal("200000.00")
    assert res.totales["ARS"].balance == Decimal("140000.00")


def test_caso_c_filtro_tipo_egreso(db_session: Session, fixtures_historial):
    """c) tipo=egreso: 7 ítems, ninguno de clase distinta de "transaccion"."""
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        tipo=TipoTransaccion.EGRESO,
        hoy=hoy,
    )

    assert res.total_items == 7
    assert len(res.items) == 7
    assert all(it.clase == "transaccion" for it in res.items)


def test_caso_d_busqueda(db_session: Session, fixtures_historial):
    """d) busqueda "zapatillas": 1 ítem, "Zapatillas 1/3"."""
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        busqueda="zapatillas",
        hoy=hoy,
    )

    assert res.total_items == 1
    assert len(res.items) == 1
    assert res.items[0].descripcion == "Zapatillas 1/3"


def test_caso_e_modo_general_billetera_mp(db_session: Session, fixtures_historial):
    """e) Modo general con billetera_id de Mercado Pago: 2 ítems (Transferencia y Rendimiento)."""
    usuario = fixtures_historial["usuario"]
    mp = fixtures_historial["mp"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="general",
        billetera_id=mp.id,
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        hoy=hoy,
    )

    assert res.total_items == 2
    clases = {it.clase for it in res.items}
    assert clases == {"transferencia", "rendimiento"}


def test_caso_f_modo_billetera_galicia(db_session: Session, fixtures_historial):
    """
    f) Modo billetera Galicia, sin fechas: 9 ítems.
       saldo_despues en orden de fecha:
       Saldo inicial 150000.00, Súper 140000.00, Pago de tarjeta 90000.00,
       "A Mercado Pago" 50000.00, Aporte a meta 45000.00, Ajuste de saldo 44000.00,
       Sueldo 244000.00.
       Kiosco y Gimnasio con saldo_despues None y cuenta_en_saldo False.
       No aparecen Farmacia ni Zapatillas.
    """
    usuario = fixtures_historial["usuario"]
    galicia = fixtures_historial["galicia"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="billetera",
        billetera_id=galicia.id,
        hoy=hoy,
    )

    assert res.total_items == 9

    # Verificar que no aparecen compras crediticias
    descs = [it.descripcion for it in res.items]
    assert not any("Farmacia" in d for d in descs)
    assert not any("Zapatillas" in d for d in descs)

    # Orden cronológico ascendente para chequear la progresión de saldo_despues
    items_cronologico = sorted(res.items, key=lambda x: (x.fecha, 0 if x.clase == "saldo_inicial" else 1))

    assert items_cronologico[0].descripcion == "Saldo inicial"
    assert items_cronologico[0].saldo_despues == Decimal("150000.00")

    assert items_cronologico[1].descripcion == "Súper"
    assert items_cronologico[1].saldo_despues == Decimal("140000.00")

    assert items_cronologico[2].descripcion == "Pago de tarjeta"
    assert items_cronologico[2].saldo_despues == Decimal("90000.00")

    assert items_cronologico[3].descripcion == "A Mercado Pago"
    assert items_cronologico[3].saldo_despues == Decimal("50000.00")

    assert items_cronologico[4].descripcion == "Aporte a meta"
    assert items_cronologico[4].saldo_despues == Decimal("45000.00")

    assert items_cronologico[5].descripcion == "Ajuste de saldo"
    assert items_cronologico[5].saldo_despues == Decimal("44000.00")

    assert items_cronologico[6].descripcion == "Sueldo"
    assert items_cronologico[6].saldo_despues == Decimal("244000.00")

    kiosco = next(it for it in res.items if it.descripcion == "Kiosco")
    assert kiosco.saldo_despues is None
    assert kiosco.cuenta_en_saldo is False

    gimnasio = next(it for it in res.items if it.descripcion == "Gimnasio")
    assert gimnasio.saldo_despues is None
    assert gimnasio.cuenta_en_saldo is False


def test_caso_g_modo_billetera_mp(db_session: Session, fixtures_historial):
    """g) Modo billetera Mercado Pago: 3 ítems. Saldo inicial 0.00, "De Galicia" 40000.00, Rendimiento 40800.00."""
    usuario = fixtures_historial["usuario"]
    mp = fixtures_historial["mp"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="billetera",
        billetera_id=mp.id,
        hoy=hoy,
    )

    assert res.total_items == 3
    items_cronologico = sorted(res.items, key=lambda x: (x.fecha, 0 if x.clase == "saldo_inicial" else 1))

    assert items_cronologico[0].descripcion == "Saldo inicial"
    assert items_cronologico[0].saldo_despues == Decimal("0.00")

    assert items_cronologico[1].descripcion == "De Galicia"
    assert items_cronologico[1].saldo_despues == Decimal("40000.00")

    assert items_cronologico[2].descripcion == "Rendimiento"
    assert items_cronologico[2].saldo_despues == Decimal("40800.00")


def test_caso_h_etiqueta_movimiento_memoria():
    """
    h) etiqueta_movimiento con objetos en memoria, una prueba por rama:
       - retiro de meta: "Retiro de meta"
       - egreso con categoría Ahorro sin meta: "Ahorro"
       - egreso en billetera de inversión: "Inversión"
       - egreso con subcategoría Banco > Tarjeta de crédito sin pago_resumen_vencimiento: "Pago de tarjeta"
       - hija de crédito en estado pendiente, de un grupo de 3, con fecha <= hoy: "Gasto"
       Cada una con su aclaración exacta.
    """
    id_cat_ahorro = uuid4()
    id_subcat_tc = uuid4()
    id_billetera_inv = uuid4()
    id_grupo_3 = uuid4()
    hoy_ref = date(2026, 10, 15)

    ctx = ContextoDefiniciones(
        grupos_cuotas_cantidades={id_grupo_3: 3},
        billeteras_inversion_ids={id_billetera_inv},
        categoria_ahorro_ids={id_cat_ahorro},
        subcategoria_tarjeta_id=id_subcat_tc,
        subcategoria_tarjeta_ids={id_subcat_tc},
        hoy=hoy_ref,
    )

    # 1. Retiro de meta
    tx_retiro = SimpleNamespace(
        tipo="ingreso",
        movimiento_meta_id=uuid4(),
        fecha=date(2026, 10, 10),
        billetera_id=uuid4(),
    )
    etq, c_como, aclr = etiqueta_movimiento(tx_retiro, ctx)
    assert etq == "Retiro de meta"
    assert c_como == "nada"
    assert aclr == "No es ingreso: sale de tu meta"

    # 2. Egreso con categoría Ahorro sin meta
    tx_ahorro = SimpleNamespace(
        tipo="egreso",
        categoria_id=id_cat_ahorro,
        movimiento_meta_id=None,
        fecha=date(2026, 10, 10),
        billetera_id=uuid4(),
    )
    etq, c_como, aclr = etiqueta_movimiento(tx_ahorro, ctx)
    assert etq == "Ahorro"
    assert c_como == "nada"
    assert aclr == "No es gasto"

    # 3. Egreso en billetera de inversión
    tx_inv = SimpleNamespace(
        tipo="egreso",
        billetera_id=id_billetera_inv,
        movimiento_meta_id=None,
        fecha=date(2026, 10, 10),
    )
    etq, c_como, aclr = etiqueta_movimiento(tx_inv, ctx)
    assert etq == "Inversión"
    assert c_como == "nada"
    assert aclr == "No cuenta: billetera de inversión"

    # 4. Egreso con subcategoría Banco > Tarjeta de crédito sin pago_resumen_vencimiento
    tx_tc = SimpleNamespace(
        tipo="egreso",
        subcategoria_id=id_subcat_tc,
        pago_resumen_vencimiento=None,
        movimiento_meta_id=None,
        billetera_id=uuid4(),
        fecha=date(2026, 10, 10),
    )
    etq, c_como, aclr = etiqueta_movimiento(tx_tc, ctx)
    assert etq == "Pago de tarjeta"
    assert c_como == "nada"
    assert aclr == "No es gasto: las compras ya se contaron"

    # 5. Hija de crédito en estado pendiente, de un grupo de 3, con fecha <= hoy: "Gasto"
    tx_hija_pend = SimpleNamespace(
        tipo="egreso",
        es_cuota_hija=True,
        es_padre_cuotas=False,
        grupo_cuotas_id=id_grupo_3,
        estado_verificacion="pendiente",
        fecha=date(2026, 10, 10),
        billetera_id=uuid4(),
        movimiento_meta_id=None,
        pago_resumen_vencimiento=None,
        categoria_id=None,
        subcategoria_id=None,
    )
    etq, c_como, aclr = etiqueta_movimiento(tx_hija_pend, ctx)
    assert etq == "Gasto"
    assert c_como == "gasto"
    assert aclr is None


def test_caso_i_get_dashboard_resumen(db_session: Session, fixtures_historial):
    """
    i) get_dashboard_resumen del usuario con hoy 2026-10-15:
       ultimos_movimientos tiene como mucho 6, incluye Farmacia, no incluye Gimnasio y todos traen etiqueta.
    """
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    # Para que Farmacia (2026-10-03) quede dentro de los primeros 6 movimientos del dashboard
    # y no desplazada por los 8 movimientos posteriores del fixture, removemos 4 movimientos posteriores.
    db_session.query(Transaccion).filter(
        Transaccion.usuario_id == usuario.id,
        Transaccion.descripcion.in_(["Kiosco", "Sueldo"])
    ).delete(synchronize_session=False)
    db_session.query(AjusteSaldo).filter(
        AjusteSaldo.billetera_id == fixtures_historial["galicia"].id
    ).delete(synchronize_session=False)
    db_session.query(RendimientoBilletera).filter(
        RendimientoBilletera.billetera_id == fixtures_historial["mp"].id
    ).delete(synchronize_session=False)
    db_session.commit()

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario, hoy=hoy)
    ultimos = resumen.get("ultimos_movimientos", [])

    assert len(ultimos) <= 6
    descs = [m["descripcion"] for m in ultimos]

    assert "Farmacia" in descs
    assert "Gimnasio" not in descs
    assert all("etiqueta" in m and m["etiqueta"] for m in ultimos)


def test_caso_j_endpoint_movimientos(db_session: Session, fixtures_historial, monkeypatch):
    """
    j) Test de GET /movimientos que devuelva 200 con los totales de (a), usando
       el patrón estándar de tests/routers para usuario autenticado.
    """
    usuario = fixtures_historial["usuario"]
    monkeypatch.setattr("app.services.historial_service.hoy_argentina", lambda: date(2026, 10, 15))

    app.dependency_overrides[get_current_user] = lambda: usuario
    app.dependency_overrides[get_db] = lambda: db_session

    try:
        client = TestClient(app)
        response = client.get(
            "/movimientos?modo=general&fecha_desde=2026-10-01&fecha_hasta=2026-10-31"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 11
        assert data["totales"]["ARS"]["gastos"] == 60000.00
        assert data["totales"]["ARS"]["ingresos"] == 200000.00
        assert data["totales"]["ARS"]["balance"] == 140000.00
        assert data["totales"]["USD"]["gastos"] == 0.00
        assert data["totales"]["USD"]["ingresos"] == 0.00
        assert data["totales"]["USD"]["balance"] == 0.00
    finally:
        app.dependency_overrides.clear()


def test_busqueda_super(db_session: Session, fixtures_historial):
    """modo general del 01/10 al 31/10 con busqueda 'SUPER': 1 ítem, 'Súper'."""
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        busqueda="SUPER",
        hoy=hoy,
    )

    assert res.total_items == 1
    assert len(res.items) == 1
    assert res.items[0].descripcion == "Súper"


def test_busqueda_mercado_pago(db_session: Session, fixtures_historial):
    """mismo pedido con busqueda 'mercado pago': 1 ítem, clase 'transferencia', 'Galicia → Mercado Pago'."""
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    res = historial_service.listar_movimientos(
        db=db_session,
        usuario_id=usuario.id,
        modo="general",
        fecha_desde=date(2026, 10, 1),
        fecha_hasta=date(2026, 10, 31),
        busqueda="mercado pago",
        hoy=hoy,
    )

    assert res.total_items == 1
    assert len(res.items) == 1
    assert res.items[0].clase == "transferencia"
    assert res.items[0].descripcion == "Galicia → Mercado Pago"


def test_dashboard_resumen_sentido_y_cuenta_como(db_session: Session, fixtures_historial):
    """
    get_dashboard_resumen: el ítem del rendimiento (si entra en los 6) trae sentido 'entra'
    y cuenta_como 'nada', y la transferencia trae tipo 'transferencia' y sentido 'neutro'.
    Si con el fixture no entran en los 6, armá el control con billetera_ids de Mercado Pago
    (ahí hay solo esos dos).
    """
    usuario = fixtures_historial["usuario"]
    hoy = date(2026, 10, 15)

    # Control específico con billetera_ids de Mercado Pago (allí hay solo transferencia y rendimiento)
    resumen_mp = dashboard_service.get_dashboard_resumen(
        db_session,
        usuario,
        billetera_ids=[fixtures_historial["mp"].id],
        hoy=hoy,
    )
    movs_mp = {m["descripcion"]: m for m in resumen_mp.get("ultimos_movimientos", [])}
    assert "Rendimiento" in movs_mp
    assert movs_mp["Rendimiento"]["sentido"] == "entra"
    assert movs_mp["Rendimiento"]["cuenta_como"] == "nada"
    assert "Galicia → Mercado Pago" in movs_mp
    assert movs_mp["Galicia → Mercado Pago"]["tipo"] == "transferencia"
    assert movs_mp["Galicia → Mercado Pago"]["sentido"] == "neutro"

    # Control en resumen general sin filtros de billetera
    resumen_gen = dashboard_service.get_dashboard_resumen(
        db_session,
        usuario,
        hoy=hoy,
    )
    movs_gen = {m["descripcion"]: m for m in resumen_gen.get("ultimos_movimientos", [])}
    if "Rendimiento" in movs_gen:
        assert movs_gen["Rendimiento"]["sentido"] == "entra"
        assert movs_gen["Rendimiento"]["cuenta_como"] == "nada"
    if "Galicia → Mercado Pago" in movs_gen:
        assert movs_gen["Galicia → Mercado Pago"]["tipo"] == "transferencia"
        assert movs_gen["Galicia → Mercado Pago"]["sentido"] == "neutro"

