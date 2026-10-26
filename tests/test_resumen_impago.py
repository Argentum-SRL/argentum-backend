"""
Tests para Fase 5a: Resumen impago y avisos.
Verifica:
1. Job procesar_vencimientos_tarjetas crea notificación RESUMEN_IMPAGO en d=0 con mensaje exacto,
   es idempotente si corre 2 veces en el día, y no crea transacciones.
2. Secuencia de avisos en d in (0, 3, 7, 14, 21): no avisa en d=1 ni en d=22.
3. Pago confirmado de 40.000: no genera aviso en ningún día.
4. Pago PENDIENTE: no bloquea el aviso y listar_pagos_proximos NO da las cuotas por cubiertas.
5. resumen_impago en TarjetaCreditoResponse: lleno con notificación sin archivar, null tras archivarla,
   null tras registrar el pago, y las notificaciones quedan archivadas.
6. Pago con excedente de 5.000 y diferencia_tipo "intereses": genera egreso en Banco > Intereses pagados
   con descripción "Intereses del resumen <últimos 4>".
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
import uuid

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
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
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
from app.services import (
    notificacion_service,
    pago_resumen_service,
    pagos_proximos_service,
    tarjeta_service,
    vencimiento_tarjeta_service,
)
from app.utils.formato import formatear_monto


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


def _crear_escenario_tarjeta_visa(db: Session):
    """
    Crea usuario, billetera, tarjeta 'Visa' con dia_cierre=24, dia_vencimiento=2 (dia_fijo).
    En el ciclo de septiembre 2026, el cierre es 2026-09-24 y el vencimiento es 2026-10-02 (viernes, hábil).
    Crea una cuota impaga con vencimiento 2026-10-02 por 85.539,63.
    """
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(u)

    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Cuenta Banco",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000000.00"),
        saldo_actual=Decimal("1000000.00"),
        es_inversion=False,
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    db.add(b)

    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=24,
        dia_vencimiento=2,
        regla_cierre="dia_fijo",
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(t)

    cat_banco = Categoria(
        id=uuid4(),
        nombre="Banco",
        tipo=TipoCategoria.EGRESO,
    )
    db.add(cat_banco)

    subcat_tc = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Tarjeta de crédito",
        orden=1,
    )
    db.add(subcat_tc)

    subcat_impuestos = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Impuestos",
        orden=10,
    )
    db.add(subcat_impuestos)

    subcat_intereses = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Intereses pagados",
        orden=11,
    )
    db.add(subcat_intereses)

    # Transacción de compra que originó la cuota
    tx_compra = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("85539.63"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 10),
        descripcion="Compra electrodomésticos",
        categoria_id=cat_banco.id,
        subcategoria_id=subcat_tc.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=t.id,
        es_cuota_hija=False,
        es_padre_cuotas=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx_compra)

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        tarjeta_id=t.id,
        transaccion_padre_id=tx_compra.id,
        monto_total=Decimal("85539.63"),
        total_financiado=Decimal("85539.63"),
        moneda=Moneda.ARS,
        cantidad_cuotas=1,
        descripcion="Compra electrodomésticos",
    )
    db.add(grupo)

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_compra.id,
        numero_cuota=1,
        monto_proyectado=Decimal("85539.63"),
        monto_real=None,
        fecha_vencimiento=date(2026, 10, 2),
        ajustada_manual=False,
        pagada=False,
    )
    db.add(cuota)

    db.commit()
    db.refresh(u)
    db.refresh(b)
    db.refresh(t)
    return u, b, t, cuota


def test_job_dia_vencimiento_y_sin_transacciones(db: Session):
    """
    Job con hoy 2026-10-02:
    - 1 notificación RESUMEN_IMPAGO con mensaje exacto "¿Pagaste el resumen de Visa de $85.539,63 que venció el 02/10?".
    - Si se corre dos veces ese día, sigue habiendo 1.
    - El job no crea ninguna transacción (misma cantidad antes y después).
    """
    u, b, t, cuota = _crear_escenario_tarjeta_visa(db)

    tx_count_before = db.query(Transaccion).count()

    # Ejecución 1 del job
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 2))

    notifs = db.query(Notificacion).filter(
        Notificacion.usuario_id == u.id,
        Notificacion.tipo == TipoNotificacion.RESUMEN_IMPAGO,
    ).all()
    assert len(notifs) == 1

    mensaje_esperado = (
        "¿Pagaste el resumen de Visa de "
        + formatear_monto(Decimal("85539.63"), "ARS")
        + " que venció el 02/10?"
    )
    assert notifs[0].mensaje == mensaje_esperado
    assert notifs[0].canal_web is True
    assert notifs[0].canal_whatsapp is False
    assert notifs[0].entidad_tipo == "tarjeta"
    assert str(notifs[0].entidad_id) == str(t.id)
    assert notifs[0].grupo_agrupacion == f"RESUMEN_IMPAGO_{t.id}_20261002_0"
    assert notifs[0].deep_link == f"/app/billeteras/{t.billetera_id}?tab=credito&tarjeta_id={t.id}"

    # Ejecución 2 del job mismo día: idempotencia
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 2))

    notifs_despues = db.query(Notificacion).filter(
        Notificacion.usuario_id == u.id,
        Notificacion.tipo == TipoNotificacion.RESUMEN_IMPAGO,
    ).all()
    assert len(notifs_despues) == 1

    # Verificación de transacciones: ninguna creada
    tx_count_after = db.query(Transaccion).count()
    assert tx_count_before == tx_count_after


def test_job_dias_posteriores_0_3_7_14_21(db: Session):
    """
    hoy 2026-10-03 (d=1): ninguna nueva.
    2026-10-05 (d=3): 1 nueva.
    2026-10-23 (d=21): 1 nueva.
    2026-10-24 (d=22): ninguna nueva.
    """
    u, b, t, cuota = _crear_escenario_tarjeta_visa(db)

    # d=0 (2026-10-02): 1 notificación
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 2))
    assert db.query(Notificacion).count() == 1

    # d=1 (2026-10-03): ninguna nueva
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 3))
    assert db.query(Notificacion).count() == 1

    # d=3 (2026-10-05): 1 nueva
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 5))
    assert db.query(Notificacion).count() == 2

    # d=21 (2026-10-23): 1 nueva
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 23))
    assert db.query(Notificacion).count() == 3

    # d=22 (2026-10-24): ninguna nueva
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 24))
    assert db.query(Notificacion).count() == 3


def test_pago_confirmado_parcial_suprime_notificaciones(db: Session):
    """
    Con un pago confirmado de 40.000 para ese vencimiento: ninguna notificación en ningún día.
    """
    u, b, t, cuota = _crear_escenario_tarjeta_visa(db)

    pago_confirmado = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("40000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 2),
        descripcion="Pago resumen Visa",
        billetera_id=b.id,
        tarjeta_id=t.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        pago_resumen_vencimiento=date(2026, 10, 2),
    )
    db.add(pago_confirmado)
    db.commit()

    # d=0 (2026-10-02)
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 2))
    assert db.query(Notificacion).count() == 0

    # d=3 (2026-10-05)
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 5))
    assert db.query(Notificacion).count() == 0

    # d=21 (2026-10-23)
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 23))
    assert db.query(Notificacion).count() == 0


def test_pago_pendiente_genera_aviso_y_no_cubre_cuotas(db: Session):
    """
    Con un egreso PENDIENTE para ese vencimiento:
    - El aviso sale igual.
    - listar_pagos_proximos NO da esas cuotas por cubiertas.
    """
    u, b, t, cuota = _crear_escenario_tarjeta_visa(db)

    pago_pendiente = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("85539.63"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 2),
        descripcion="Pago resumen Visa pendiente",
        billetera_id=b.id,
        tarjeta_id=t.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.RECURRENTE,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        pago_resumen_vencimiento=date(2026, 10, 2),
    )
    db.add(pago_pendiente)
    db.commit()

    # El job corre y emite aviso porque el pago no está confirmado
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 2))
    assert db.query(Notificacion).count() == 1

    # listar_pagos_proximos no da las cuotas por cubiertas
    proximos = pagos_proximos_service.listar_pagos_proximos(
        db, u, fecha_fin_ciclo=date(2026, 10, 15), hoy=date(2026, 10, 1)
    )
    items_tarjeta = [p for p in proximos if p.get("tipo") == "tarjeta" or "Visa" in str(p.get("nombre", ""))]
    assert len(items_tarjeta) > 0
    # El monto debe reflejar la deuda sin descontar el pago pendiente
    monto_comprometido = sum(Decimal(str(item["monto"])) for item in items_tarjeta)
    assert monto_comprometido >= Decimal("85539.63")


def test_resumen_impago_lifecycle_y_archivado(db: Session):
    """
    - tarjeta con resumen_impago lleno con la notificación sin archivar;
    - null después de archivarla;
    - null después de registrar un pago de ese resumen, y en ese caso las notificaciones
      del resumen quedan archivadas.
    """
    u, b, t, cuota = _crear_escenario_tarjeta_visa(db)

    # Corremos job para generar notificación
    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db, hoy=date(2026, 10, 2))
    notif = db.query(Notificacion).filter(
        Notificacion.usuario_id == u.id,
        Notificacion.tipo == TipoNotificacion.RESUMEN_IMPAGO,
    ).first()
    assert notif is not None
    assert notif.archivada is False

    # 1. Tarjeta con resumen_impago lleno con la notificación sin archivar
    tc_data = tarjeta_service.obtener_tarjeta(db, u.id, t.id)
    assert tc_data.resumen_impago is not None
    assert tc_data.resumen_impago["vencimiento"] == date(2026, 10, 2)
    assert tc_data.resumen_impago["monto"] == Decimal("85539.63")
    assert tc_data.resumen_impago["notificacion_id"] == notif.id

    # 2. null después de archivarla
    notificacion_service.archivar_notificacion(db, u.id, notif.id)
    db.refresh(notif)
    assert notif.archivada is True

    tc_data_archivada = tarjeta_service.obtener_tarjeta(db, u.id, t.id)
    assert tc_data_archivada.resumen_impago is None

    # Desarchivamos o creamos otra notificación para d=3 para probar el pago
    notif_d3 = Notificacion(
        id=uuid4(),
        usuario_id=u.id,
        tipo=TipoNotificacion.RESUMEN_IMPAGO,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        entidad_tipo="tarjeta",
        entidad_id=t.id,
        grupo_agrupacion=f"RESUMEN_IMPAGO_{t.id}_20261002_3",
        mensaje="Aviso d3",
        canal_web=True,
        canal_whatsapp=False,
        archivada=False,
    )
    db.add(notif_d3)
    db.commit()

    tc_data_con_d3 = tarjeta_service.obtener_tarjeta(db, u.id, t.id)
    assert tc_data_con_d3.resumen_impago is not None

    # 3. Registrar pago del resumen
    pago_resumen_service.pagar_resumen_tarjeta(
        db,
        u.id,
        t.id,
        monto=Decimal("85539.63"),
        fecha_resumen=date(2026, 10, 2),
        moneda=Moneda.ARS,
        billetera_id=b.id,
    )

    # Tarjeta queda con resumen_impago = None
    tc_data_pagada = tarjeta_service.obtener_tarjeta(db, u.id, t.id)
    assert tc_data_pagada.resumen_impago is None

    # Y las notificaciones de ese resumen quedan archivadas
    todas_notifs = db.query(Notificacion).filter(
        Notificacion.usuario_id == u.id,
        Notificacion.tipo == TipoNotificacion.RESUMEN_IMPAGO,
    ).all()
    assert len(todas_notifs) >= 2
    for n in todas_notifs:
        assert n.archivada is True


def test_pago_con_excedente_intereses(db: Session):
    """
    Pago con excedente de 5.000 y diferencia_tipo 'intereses':
    - Egreso en Banco > 'Intereses pagados' con descripción 'Intereses del resumen <últimos 4>'.
    """
    u, b, t, cuota = _crear_escenario_tarjeta_visa(db)

    total_esperado = Decimal("85539.63")
    excedente = Decimal("5000.00")
    monto_pago = total_esperado + excedente

    tx = pago_resumen_service.pagar_resumen_tarjeta(
        db,
        u.id,
        t.id,
        monto=monto_pago,
        fecha_resumen=date(2026, 10, 2),
        moneda=Moneda.ARS,
        billetera_id=b.id,
        diferencia_tipo="intereses",
    )
    assert getattr(tx, "monto_diferencia", None) == excedente

    # Buscar egreso de intereses
    ultimos_4 = t.nombre[-4:] if len(t.nombre) >= 4 else t.nombre
    desc_esperada = f"Intereses del resumen {ultimos_4}"

    tx_intereses = db.query(Transaccion).filter(
        Transaccion.usuario_id == u.id,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.monto == excedente,
        Transaccion.descripcion == desc_esperada,
    ).first()

    assert tx_intereses is not None
    assert tx_intereses.categoria is not None
    assert tx_intereses.categoria.nombre.lower() == "banco"
    assert tx_intereses.subcategoria is not None
    assert tx_intereses.subcategoria.nombre == "Intereses"
    assert tx_intereses.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA

