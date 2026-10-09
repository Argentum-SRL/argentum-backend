"""
tests/test_dashboard_ciclo.py — Tests de ciclo de dashboard y disponible libre (fase_dash_ciclo).

Cubre los 12 casos especificados con datos del caso real:
- usuario con ciclo día 28;
- billeteras ARS: Efectivo Pesos $0, Mercado Pago $318.545,80 y Banco Nación $1.460.000;
- tarjeta •••• 1111 de Banco Nación, con cierre 10 y vencimiento 3;
- compra con crédito de $10.000 en 12 cuotas con fecha 2026-10-07.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
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
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.factura import Factura
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import (
    dashboard_service,
    pagos_proximos_service,
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
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def fixtures_caso_real(db_session: Session):
    """Crea los fixtures del caso real: usuario día 28, 3 billeteras, tarjeta 1111 y compra en 12 cuotas."""
    usuario = Usuario(
        id=uuid4(),
        email="caso_real@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Usuario",
        apellido="Real",
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="28",
    )
    db_session.add(usuario)
    db_session.flush()

    b_efectivo = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Efectivo Pesos",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("0.00"),
        saldo_actual=Decimal("0.00"),
        es_efectivo=True,
        estado=EstadoBilletera.ACTIVA,
    )
    b_mp = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("318545.80"),
        saldo_actual=Decimal("318545.80"),
        estado=EstadoBilletera.ACTIVA,
    )
    b_nacion = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco Nación",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1460000.00"),
        saldo_actual=Decimal("1460000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add_all([b_efectivo, b_mp, b_nacion])
    db_session.flush()

    cat_compras = Categoria(
        id=uuid4(),
        nombre="Compras",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db_session.add(cat_compras)
    db_session.flush()

    tarjeta_1111 = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_nacion.id,
        nombre="•••• 1111",
        red=RedTarjeta.VISA,
        dia_cierre=10,
        dia_vencimiento=3,
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA,
    )
    db_session.add(tarjeta_1111)
    db_session.flush()

    # Compra con crédito de $10.000 en 12 cuotas con fecha 2026-10-07
    tx_data = TransaccionCreate(
        monto=Decimal("10000.00"),
        tipo=TipoTransaccion.EGRESO,
        metodo_pago=MetodoPago.CREDITO,
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 7),
        billetera_id=b_nacion.id,
        tarjeta_id=tarjeta_1111.id,
        categoria_id=cat_compras.id,
        descripcion="Compra 12 cuotas",
        info_cuotas=InfoCuotas(
            cantidad_cuotas=12,
            total_cuotas=12,
            cuota_inicial=1,
            tiene_interes=False,
            monto_total=Decimal("10000.00"),
        ),
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data, commit=False)
    db_session.commit()

    return {
        "usuario": usuario,
        "b_efectivo": b_efectivo,
        "b_mp": b_mp,
        "b_nacion": b_nacion,
        "tarjeta_1111": tarjeta_1111,
        "categoria": cat_compras,
    }


def _crear_suscripcion(
    db: Session,
    usuario_id: uuid.UUID,
    nombre: str,
    monto: Decimal,
    proximo_cobro: date,
    moneda: Moneda = Moneda.ARS,
    billetera_id: uuid.UUID | None = None,
) -> Suscripcion:
    sub = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre=nombre,
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=proximo_cobro,
        estado=EstadoSuscripcion.ACTIVA,
        billetera_id=billetera_id,
    )
    db.add(sub)
    db.flush()
    hist = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=sub.id,
        monto=monto,
        moneda=moneda,
        vigente_desde=date(2026, 1, 1),
    )
    db.add(hist)
    db.commit()
    return sub


def _crear_factura(
    db: Session,
    usuario_id: uuid.UUID,
    descripcion: str,
    monto: Decimal,
    fecha_vencimiento: date,
    estado: str = "pendiente",
    moneda: Moneda = Moneda.ARS,
    pagada_auto: bool = False,
) -> Factura:
    fac = Factura(
        id=uuid4(),
        usuario_id=usuario_id,
        descripcion=descripcion,
        monto=monto,
        moneda=moneda,
        fecha_vencimiento=fecha_vencimiento,
        estado=estado,
        pagada_automaticamente=pagada_auto,
        origen="whatsapp_foto",
    )
    db.add(fac)
    db.commit()
    return fac


def test_caso_01_caso_real_hoy_2026_10_08(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 1: Con hoy 2026-10-08: proximos_pagos ARS vacío; saldo_total = saldo_disponible = 1778545.80; compromisos vacío; periodo.fecha_fin 2026-10-27."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)

    # periodo.fecha_fin es 2026-10-27
    assert resumen["periodo"]["fecha_fin"] == "2026-10-27"

    # proximos_pagos en ARS vacío
    pagos_ars = [p for p in resumen["proximos_pagos"] if p["moneda"] == "ARS"]
    assert len(pagos_ars) == 0

    # saldo_disponible
    saldo_ars = resumen["saldo_disponible"]["ars"]
    assert Decimal(str(saldo_ars["saldo_total"])) == Decimal("1778545.80")
    assert Decimal(str(saldo_ars["saldo_disponible"])) == Decimal("1778545.80")
    assert len(saldo_ars["compromisos"]) == 0


def test_caso_02_caso_real_hoy_2026_10_28(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 2: Con hoy 2026-10-28: proximos_pagos ARS tiene resumen_tarjeta 2026-11-03 $833.33; saldo_disponible 1777712.47 y compromisos = ese ítem."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 28))

    usuario = fixtures_caso_real["usuario"]
    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)

    pagos_ars = [p for p in resumen["proximos_pagos"] if p["moneda"] == "ARS"]
    assert len(pagos_ars) == 1
    pago = pagos_ars[0]
    assert pago["tipo"] == "resumen_tarjeta"
    assert pago["fecha_cobro"] == "2026-11-03"
    assert Decimal(str(pago["monto"])) == Decimal("833.33")

    saldo_ars = resumen["saldo_disponible"]["ars"]
    assert Decimal(str(saldo_ars["saldo_disponible"])) == Decimal("1777712.47")
    assert len(saldo_ars["compromisos"]) == 1
    comp = saldo_ars["compromisos"][0]
    assert comp["tipo"] == "resumen_tarjeta"
    assert comp["fecha_cobro"] == "2026-11-03"
    assert Decimal(str(comp["monto"])) == Decimal("833.33")


def test_caso_03_suscripcion_limite_ciclo(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 3: Suscripción último día del ciclo entra; otra un día después no entra."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    # Fin del ciclo: 2026-10-27
    sub_dentro = _crear_suscripcion(db_session, usuario.id, "Sub Ultimo Dia", Decimal("1500.00"), date(2026, 10, 27))
    sub_fuera = _crear_suscripcion(db_session, usuario.id, "Sub Dia Despues", Decimal("2000.00"), date(2026, 10, 28))

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)
    ids_pagos = [str(uuid.UUID(str(p["id"]))) for p in resumen["proximos_pagos"]]
    assert str(sub_dentro.id) in ids_pagos
    assert str(sub_fuera.id) not in ids_pagos


def test_caso_04_suscripcion_vencida_ayer(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 4: Suscripción con próximo cobro ayer entra como vencida y se resta."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    sub_vencida = _crear_suscripcion(db_session, usuario.id, "Sub Ayer", Decimal("5000.00"), date(2026, 10, 7))

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)
    pagos = [p for p in resumen["proximos_pagos"] if str(uuid.UUID(str(p["id"]))) == str(sub_vencida.id)]
    assert len(pagos) == 1
    assert pagos[0]["es_vencido"] is True
    assert pagos[0]["dias_restantes"] < 0

    saldo_ars = resumen["saldo_disponible"]["ars"]
    assert Decimal(str(saldo_ars["suscripciones_pendientes"])) == Decimal("5000.00")
    assert Decimal(str(saldo_ars["saldo_disponible"])) == Decimal("1778545.80") - Decimal("5000.00")


def test_caso_05_siete_suscripciones_recorte_y_compromisos(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 5: 7 suscripciones ARS dentro del ciclo ($1k a $7k): proximos_pagos trae 5, compromisos trae 7, saldo_disponible = saldo_total - 28.000."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    for i in range(1, 8):
        _crear_suscripcion(db_session, usuario.id, f"Sub {i}", Decimal(str(i * 1000)), date(2026, 10, 10 + i))

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)
    pagos_ars = [p for p in resumen["proximos_pagos"] if p["moneda"] == "ARS"]
    assert len(pagos_ars) == 5

    saldo_ars = resumen["saldo_disponible"]["ars"]
    assert len(saldo_ars["compromisos"]) == 7
    total_descontado = sum(Decimal(str(c["monto"])) for c in saldo_ars["compromisos"])
    assert total_descontado == Decimal("28000.00")
    assert Decimal(str(saldo_ars["saldo_disponible"])) == Decimal(str(saldo_ars["saldo_total"])) - Decimal("28000.00")


def test_caso_06_facturas_reglas(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 6: Facturas: pendiente dentro del ciclo se resta; pagada aparece como pagada y no se resta; pendiente después del ciclo no aparece."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    # 1. Pendiente dentro del ciclo (vence 2026-10-15)
    f_pend = _crear_factura(db_session, usuario.id, "Gas Pendiente", Decimal("12000.00"), date(2026, 10, 15), estado="pendiente")
    # 2. Pagada dentro del ciclo (vence 2026-10-20, pagada automáticamente)
    f_paga = _crear_factura(db_session, usuario.id, "Luz Pagada", Decimal("8000.00"), date(2026, 10, 20), estado="pagada", pagada_auto=True)
    # 3. Pendiente después del ciclo (vence 2026-10-30)
    f_futura = _crear_factura(db_session, usuario.id, "Agua Futura", Decimal("5000.00"), date(2026, 10, 30), estado="pendiente")

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)
    ids_pagos = {p["id"]: p for p in resumen["proximos_pagos"]}

    assert str(f_pend.id) in ids_pagos
    assert str(f_paga.id) in ids_pagos
    assert str(f_futura.id) not in ids_pagos

    # Pagada aparece con estado_factura 'pagada'
    assert ids_pagos[str(f_paga.id)]["estado_factura"] == "pagada"

    # En compromisos solo está f_pend, no f_paga
    saldo_ars = resumen["saldo_disponible"]["ars"]
    ids_compromisos = [c["id"] for c in saldo_ars["compromisos"]]
    assert str(f_pend.id) in ids_compromisos
    assert str(f_paga.id) not in ids_compromisos
    assert Decimal(str(saldo_ars["facturas_pendientes"])) == Decimal("12000.00")
    assert Decimal(str(saldo_ars["saldo_disponible"])) == Decimal(str(saldo_ars["saldo_total"])) - Decimal("12000.00")


def test_caso_07_multimoneda(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 7: Suscripción de US$10 y otra de $5.000 dentro del ciclo: en usd se restan 10 y en ars 5.000."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    # Agregar billetera USD con $100
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Dólares",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("100.00"),
        saldo_actual=Decimal("100.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db_session.add(b_usd)
    db_session.commit()

    _crear_suscripcion(db_session, usuario.id, "Sub ARS", Decimal("5000.00"), date(2026, 10, 15), moneda=Moneda.ARS)
    _crear_suscripcion(db_session, usuario.id, "Sub USD", Decimal("10.00"), date(2026, 10, 15), moneda=Moneda.USD)

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)
    saldo_ars = resumen["saldo_disponible"]["ars"]
    saldo_usd = resumen["saldo_disponible"]["usd"]

    assert Decimal(str(saldo_ars["suscripciones_pendientes"])) == Decimal("5000.00")
    assert Decimal(str(saldo_ars["saldo_disponible"])) == Decimal("1778545.80") - Decimal("5000.00")

    assert Decimal(str(saldo_usd["suscripciones_pendientes"])) == Decimal("10.00")
    assert Decimal(str(saldo_usd["saldo_disponible"])) == Decimal("100.00") - Decimal("10.00")


def test_caso_08_filtro_billeteras(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 8: Con billetera_ids=[Banco Nación] y hoy 2026-10-28 se resta el resumen •••• 1111. Con [Mercado Pago] no se resta y saldo_total es solo Mercado Pago."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 28))

    usuario = fixtures_caso_real["usuario"]
    b_nacion = fixtures_caso_real["b_nacion"]
    b_mp = fixtures_caso_real["b_mp"]

    # 1. Filtro Banco Nación
    res_nacion = dashboard_service.get_dashboard_resumen(db_session, usuario, billetera_ids=[b_nacion.id])
    saldo_nac = res_nacion["saldo_disponible"]["ars"]
    assert Decimal(str(saldo_nac["saldo_total"])) == Decimal("1460000.00")
    assert Decimal(str(saldo_nac["cuotas_pendientes"])) == Decimal("833.33")
    assert Decimal(str(saldo_nac["saldo_disponible"])) == Decimal("1460000.00") - Decimal("833.33")

    # 2. Filtro Mercado Pago
    res_mp = dashboard_service.get_dashboard_resumen(db_session, usuario, billetera_ids=[b_mp.id])
    saldo_mp = res_mp["saldo_disponible"]["ars"]
    assert Decimal(str(saldo_mp["saldo_total"])) == Decimal("318545.80")
    assert Decimal(str(saldo_mp["cuotas_pendientes"])) == Decimal("0.00")
    assert Decimal(str(saldo_mp["saldo_disponible"])) == Decimal("318545.80")


def test_caso_09_consistencia_total_compromisos_y_unicidad(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 9: Con casos 2 a 7 juntos: saldo_total - saldo_disponible = suma de compromisos; cada ítem no pagado está en compromisos una sola vez."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 28))

    usuario = fixtures_caso_real["usuario"]
    # Agregar suscripciones
    _crear_suscripcion(db_session, usuario.id, "Sub A", Decimal("3000.00"), date(2026, 11, 5))
    _crear_suscripcion(db_session, usuario.id, "Sub B", Decimal("4000.00"), date(2026, 11, 10))
    # Factura pendiente y pagada
    _crear_factura(db_session, usuario.id, "Factura Pend", Decimal("6000.00"), date(2026, 11, 8), estado="pendiente")
    _crear_factura(db_session, usuario.id, "Factura Paga", Decimal("2000.00"), date(2026, 11, 9), estado="pagada", pagada_auto=True)

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)
    pagos_lista = pagos_proximos_service.listar_pagos_proximos(db_session, usuario, date(2026, 11, 27), hoy=date(2026, 10, 28))

    saldo_ars = resumen["saldo_disponible"]["ars"]
    diferencia = Decimal(str(saldo_ars["saldo_total"])) - Decimal(str(saldo_ars["saldo_disponible"]))
    suma_compromisos = sum(Decimal(str(c["monto"])) for c in saldo_ars["compromisos"])
    assert diferencia == suma_compromisos

    # Unicidad: cada ítem no pagado está una sola vez
    compromisos_keys = [(c["id"], c["tipo"]) for c in saldo_ars["compromisos"]]
    assert len(compromisos_keys) == len(set(compromisos_keys))

    # Cada ítem no pagado de listar_pagos_proximos está en compromisos
    items_no_pagados = [p for p in pagos_lista if p["moneda"] == "ARS" and p.get("estado_factura") != "pagada"]
    assert len(items_no_pagados) == len(saldo_ars["compromisos"])


def test_caso_10_compra_en_un_pago_dentro_del_resumen(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 10: Compra en 1 pago que vence dentro del ciclo aparece solo dentro del resumen, no como cuota suelta."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 28))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 28))

    usuario = fixtures_caso_real["usuario"]
    tarjeta = fixtures_caso_real["tarjeta_1111"]
    cat = fixtures_caso_real["categoria"]

    # Compra en 1 pago
    tx_data = TransaccionCreate(
        monto=Decimal("5000.00"),
        tipo=TipoTransaccion.EGRESO,
        metodo_pago=MetodoPago.CREDITO,
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 5),
        billetera_id=fixtures_caso_real["b_nacion"].id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat.id,
        descripcion="Compra 1 pago",
        info_cuotas=InfoCuotas(
            cantidad_cuotas=1,
            total_cuotas=1,
            cuota_inicial=1,
            tiene_interes=False,
            monto_total=Decimal("5000.00"),
        ),
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data, commit=True)

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)
    pagos = resumen["proximos_pagos"]
    # Solo resumen_tarjeta, no cuota suelta
    tipos_pagos = [p["tipo"] for p in pagos if p["moneda"] == "ARS"]
    assert "cuota" not in tipos_pagos
    assert "resumen_tarjeta" in tipos_pagos


def test_caso_11_deuda_vencida_resumen_y_pago_cubierto(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 11: Cuota vencida el mes pasado sin pagar y sin pago de resumen aparece como 'Resumen •••• XXXX' vencido. Cubierta por pago no aparece ni se resta."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    b_nacion = fixtures_caso_real["b_nacion"]

    # Tarjeta 2222 con cierre 5 y vencimiento 28 (el resumen actual vencerá 2026-10-28, fuera del ciclo que termina 2026-10-27)
    tarjeta_2222 = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_nacion.id,
        nombre="•••• 2222",
        red=RedTarjeta.VISA,
        dia_cierre=5,
        dia_vencimiento=28,
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA,
    )
    db_session.add(tarjeta_2222)
    db_session.flush()

    # Compra vieja con cuota vencida el 2026-09-28
    tx_data = TransaccionCreate(
        monto=Decimal("6000.00"),
        tipo=TipoTransaccion.EGRESO,
        metodo_pago=MetodoPago.CREDITO,
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 1),
        billetera_id=b_nacion.id,
        tarjeta_id=tarjeta_2222.id,
        categoria_id=fixtures_caso_real["categoria"].id,
        descripcion="Compra vieja",
        info_cuotas=InfoCuotas(
            cantidad_cuotas=2,
            total_cuotas=2,
            cuota_inicial=1,
            tiene_interes=False,
            monto_total=Decimal("6000.00"),
        ),
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, tx_data, commit=True)

    # El resumen actual vence el 2026-10-28 (después del fin de ciclo 2026-10-27).
    # Pero la cuota 1 venció el 2026-09-28 y está impaga -> debe aparecer ítem 'Resumen •••• 2222' vencido
    resumen1 = dashboard_service.get_dashboard_resumen(db_session, usuario)
    items_2222 = [p for p in resumen1["proximos_pagos"] if "2222" in p["nombre"]]
    assert len(items_2222) == 1
    assert items_2222[0]["es_vencido"] is True
    assert items_2222[0]["fecha_cobro"] == "2026-09-28"

    # Ahora registramos un pago de resumen para cubrir ese vencimiento
    tx_pago = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_nacion.id,
        fecha=date(2026, 9, 29),
        monto=Decimal("3000.00"),
        tipo=TipoTransaccion.EGRESO,
        categoria_id=fixtures_caso_real["categoria"].id,
        descripcion="Pago resumen 2222",
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        tarjeta_id=tarjeta_2222.id,
        pago_resumen_vencimiento=date(2026, 9, 28),
    )
    db_session.add(tx_pago)
    db_session.commit()

    # Con el pago registrado que cubre el vencimiento, la cuota está cubierta y no aparece como deuda vencida
    resumen2 = dashboard_service.get_dashboard_resumen(db_session, usuario)
    items_2222_post = [p for p in resumen2["proximos_pagos"] if "2222" in p["nombre"]]
    assert len(items_2222_post) == 0


def test_caso_12_cards_decision_6_filtro_ciclo(db_session: Session, fixtures_caso_real, monkeypatch):
    """Caso 12: Un movimiento un día antes del inicio y otro un día después del fin quedan afuera; uno dentro entra."""
    monkeypatch.setattr("app.utils.fecha.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.dashboard_service.hoy_argentina", lambda: date(2026, 10, 8))
    monkeypatch.setattr("app.services.pagos_proximos_service.hoy_argentina", lambda: date(2026, 10, 8))

    usuario = fixtures_caso_real["usuario"]
    b_efectivo = fixtures_caso_real["b_efectivo"]
    cat = fixtures_caso_real["categoria"]

    # Ciclo actual: 2026-09-28 al 2026-10-27
    # 1. Movimiento un día antes del inicio: 2026-09-27
    tx_antes = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_efectivo.id,
        fecha=date(2026, 9, 27),
        monto=Decimal("111.00"),
        tipo=TipoTransaccion.EGRESO,
        categoria_id=cat.id,
        descripcion="Movimiento Antes",
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        metodo_pago=MetodoPago.EFECTIVO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    # 2. Movimiento dentro del ciclo: 2026-10-05
    tx_dentro = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_efectivo.id,
        fecha=date(2026, 10, 5),
        monto=Decimal("222.00"),
        tipo=TipoTransaccion.EGRESO,
        categoria_id=cat.id,
        descripcion="Movimiento Dentro",
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        metodo_pago=MetodoPago.EFECTIVO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    # 3. Movimiento un día después del fin: 2026-10-28
    tx_despues = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b_efectivo.id,
        fecha=date(2026, 10, 28),
        monto=Decimal("333.00"),
        tipo=TipoTransaccion.EGRESO,
        categoria_id=cat.id,
        descripcion="Movimiento Despues",
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        metodo_pago=MetodoPago.EFECTIVO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add_all([tx_antes, tx_dentro, tx_despues])
    db_session.commit()

    resumen = dashboard_service.get_dashboard_resumen(db_session, usuario)

    # ultimos_movimientos solo debe contener tx_dentro
    descripciones = [m["descripcion"] for m in resumen["ultimos_movimientos"]]
    assert "Movimiento Dentro" in descripciones
    assert "Movimiento Antes" not in descripciones
    assert "Movimiento Despues" not in descripciones

    # balance.ars.egresos debe reflejar solo tx_dentro (222.00)
    assert resumen["balance"]["ars"]["egresos"] == 222.0
