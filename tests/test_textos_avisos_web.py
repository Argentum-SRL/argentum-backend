"""Pruebas de textos exactos para avisos de la campanita web (N1 a N17) y servicios."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import sqlite3
from types import SimpleNamespace
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, types
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.reglas_texto_web import problemas_texto_web
from app.core.reglas_texto_wpp import nombre_tarjeta_wpp, problemas_texto_wpp
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.subcategoria import Subcategoria
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
from app.schemas.transaccion import TransaccionCreate
from app.services import (
    notificacion_scheduler_service,
    transaccion_service,
    vencimiento_tarjeta_service,
)
from app.services.textos_avisos_web import (
    texto_billetera_sin_saldo,
    texto_cambio_contrasena,
    texto_cambio_email,
    texto_cobros_suscripcion_faltaban,
    texto_cuota_sin_tarjeta_vence,
    texto_factura_vence,
    texto_inactividad,
    texto_meta_cumplida,
    texto_presupuesto_limite,
    texto_presupuesto_superado,
    texto_reintegro_pendiente,
    texto_resumen_ciclo,
    texto_resumen_impago,
    texto_resumen_semanal,
    texto_resumen_tarjeta_vence,
    texto_suscripcion_hoy,
    texto_suscripcion_proxima,
)
from app.utils.fecha import TZ_ARGENTINA


def test_n1_cambio_contrasena():
    txt = texto_cambio_contrasena()
    assert txt == (
        "Se cambió la contraseña de tu cuenta.\n"
        "Si no fuiste vos, cambiala ya."
    )
    assert problemas_texto_web(txt) == []


def test_n2_cambio_email():
    txt = texto_cambio_email()
    assert txt == (
        "Se cambió el email de tu cuenta.\n"
        "Si no fuiste vos, cambiá tu contraseña ya."
    )
    assert problemas_texto_web(txt) == []


def test_n3_billetera_sin_saldo():
    txt = texto_billetera_sin_saldo("Mercado Pago")
    assert txt == "Tu billetera Mercado Pago se quedó sin saldo."
    assert problemas_texto_web(txt) == []


def test_n4_meta_cumplida():
    txt = texto_meta_cumplida("Vacaciones", Decimal("1500000"), "ARS")
    assert txt == "¡Completaste tu meta Vacaciones!\nJuntaste $1.500.000."
    assert problemas_texto_web(txt) == []


def test_n5_factura_vence():
    hoy = date(2026, 11, 17)
    txt_3 = texto_factura_vence("Edenor", Decimal("12345.67"), "ARS", date(2026, 11, 20), hoy)
    assert txt_3 == "La factura de Edenor de $12.345,67 vence el 20/11."
    assert problemas_texto_web(txt_3) == []

    txt_hoy = texto_factura_vence("Edenor", Decimal("12345.67"), "ARS", hoy, hoy)
    assert txt_hoy == "La factura de Edenor de $12.345,67 vence hoy."
    assert problemas_texto_web(txt_hoy) == []


def test_n6_resumen_tarjeta_vence():
    tarjeta = SimpleNamespace(
        red="visa",
        billetera=SimpleNamespace(nombre="Galicia"),
        nombre="•••• 1506",
    )
    txt = texto_resumen_tarjeta_vence(
        nombre_tarjeta_wpp(tarjeta),
        date(2026, 11, 22),
        date(2026, 12, 4),
    )
    assert txt == "El resumen de tu Visa Galicia •••• 1506 cierra el 22/11 y vence el 4/12."
    assert problemas_texto_web(txt) == []


def test_n7_cuota_sin_tarjeta_vence():
    txt = texto_cuota_sin_tarjeta_vence(
        3, 6, "Heladera", date(2026, 11, 20), Decimal("45000.5"), "ARS"
    )
    assert txt == "La cuota 3 de 6 de Heladera vence el 20/11: $45.000,50."
    assert problemas_texto_web(txt) == []


def test_n8_suscripcion_hoy():
    txt = texto_suscripcion_hoy("Netflix", 9999, "ARS")
    assert txt == "Hoy se cobra Netflix: $9.999."
    assert problemas_texto_web(txt) == []


def test_n9_suscripcion_proxima():
    hoy = date(2026, 11, 17)
    txt_3 = texto_suscripcion_proxima("Spotify", 4500, "ARS", date(2026, 11, 20), hoy)
    assert txt_3 == "Spotify se cobra el 20/11: $4.500."
    assert problemas_texto_web(txt_3) == []

    txt_1 = texto_suscripcion_proxima("Spotify", 10, "USD", date(2026, 11, 18), hoy)
    assert txt_1 == "Mañana se cobra Spotify: US$10."
    assert problemas_texto_web(txt_1) == []


def test_n10_inactividad():
    txt = texto_inactividad(7)
    assert txt == (
        "Hace 7 días que no cargás nada.\n"
        "Cargá tus gastos para ver en qué se va tu plata."
    )
    assert problemas_texto_web(txt) == []


def test_n11_resumen_semanal():
    txt_1 = texto_resumen_semanal(
        ingresos_ars=100000, gastos_ars=80000, categoria_top="Supermercado"
    )
    assert txt_1 == (
        "Esta semana entraron $100.000 y gastaste $80.000.\n"
        "Lo que más pesó: Supermercado."
    )
    assert problemas_texto_web(txt_1) == []

    txt_sin_cat = texto_resumen_semanal(ingresos_ars=100000, gastos_ars=80000)
    assert txt_sin_cat == "Esta semana entraron $100.000 y gastaste $80.000."
    assert problemas_texto_web(txt_sin_cat) == []

    txt_2 = texto_resumen_semanal(
        ingresos_ars=100000,
        gastos_ars=80000,
        ingresos_usd=100,
        gastos_usd=20,
        categoria_top="Supermercado",
    )
    assert txt_2 == (
        "Tu resumen de la semana.\n"
        "- En pesos: entraron $100.000 y gastaste $80.000.\n"
        "- En dólares: entraron US$100 y gastaste US$20.\n"
        "Lo que más pesó: Supermercado."
    )
    assert problemas_texto_web(txt_2) == []


def test_n12_resumen_ciclo():
    hormiga = [
        {"categoria": "Café", "total": 18000, "cantidad": 12},
        {"categoria": "Kiosco", "total": 9000, "cantidad": 1},
    ]
    txt_ars = texto_resumen_ciclo(
        ingresos_ars=500000,
        gastos_ars=420000,
        categoria_top="Comida",
        monto_categoria_top=95000,
        gastos_hormiga=hormiga,
    )
    assert txt_ars == (
        "Terminó tu mes: te sobraron $80.000.\n"
        "Entraron $500.000 y gastaste $420.000.\n"
        "Lo que más pesó: Comida ($95.000).\n"
        "Gastos chicos que se repiten:\n"
        "- Café: $18.000 en 12 compras\n"
        "- Kiosco: $9.000 en 1 compra"
    )
    assert problemas_texto_web(txt_ars) == []

    txt_neg = texto_resumen_ciclo(ingresos_ars=400000, gastos_ars=450000)
    assert txt_neg == (
        "Terminó tu mes: gastaste $50.000 más de lo que entró.\n"
        "Entraron $400.000 y gastaste $450.000."
    )
    assert problemas_texto_web(txt_neg) == []

    txt_empate = texto_resumen_ciclo(ingresos_ars=300000, gastos_ars=300000)
    assert txt_empate.splitlines()[0] == "Terminó tu mes: gastaste lo mismo que entró."
    assert problemas_texto_web(txt_empate) == []

    hormiga_7 = [
        {"categoria": "Café", "total": 18000, "cantidad": 12},
        {"categoria": "Kiosco", "total": 9000, "cantidad": 7},
    ]
    txt_ambas = texto_resumen_ciclo(
        ingresos_ars=500000,
        gastos_ars=420000,
        ingresos_usd=100,
        gastos_usd=20,
        categoria_top="Comida",
        monto_categoria_top=95000,
        gastos_hormiga=hormiga_7,
    )
    assert txt_ambas == (
        "Terminó tu mes.\n"
        "- En pesos: entraron $500.000 y gastaste $420.000.\n"
        "- En dólares: entraron US$100 y gastaste US$20.\n"
        "Lo que más pesó: Comida ($95.000).\n"
        "Gastos chicos que se repiten:\n"
        "- Café: $18.000 en 12 compras\n"
        "- Kiosco: $9.000 en 7 compras"
    )
    assert len(txt_ambas.splitlines()) == 7
    assert problemas_texto_web(txt_ambas) == []


def test_n13_presupuesto_superado():
    txt = texto_presupuesto_superado("Comida", 120000, 100000, "ARS")
    assert txt == "Te pasaste del presupuesto Comida: gastaste $120.000 de $100.000."
    assert problemas_texto_web(txt) == []
    assert problemas_texto_wpp(txt) == []


def test_n14_presupuesto_limite():
    txt = texto_presupuesto_limite(80, "Comida", 80000, 100000, "ARS")
    assert txt == "Ya usaste el 80% del presupuesto Comida: $80.000 de $100.000."
    assert problemas_texto_web(txt) == []
    assert problemas_texto_wpp(txt) == []


def test_n15_reintegro_pendiente():
    txt = texto_reintegro_pendiente(25000, "ARS", "Súper")
    assert txt == "¿Te llegó el reintegro de $25.000 de Súper?"
    assert problemas_texto_web(txt) == []


def test_n16_resumen_impago():
    tarjeta = SimpleNamespace(
        red="visa",
        billetera=SimpleNamespace(nombre="Galicia"),
        nombre="•••• 1506",
    )
    txt = texto_resumen_impago(
        nombre_tarjeta_wpp(tarjeta),
        date(2026, 11, 10),
        120000,
        "ARS",
    )
    assert txt == (
        "El resumen de tu Visa Galicia •••• 1506 venció el 10/11: $120.000.\n"
        "¿Ya lo pagaste?"
    )
    assert problemas_texto_web(txt) == []


def test_n17_cobros_suscripcion_faltaban():
    txt_3 = texto_cobros_suscripcion_faltaban(3, "Netflix")
    assert txt_3 == "Se anotaron 3 cobros de Netflix que faltaban."
    assert problemas_texto_web(txt_3) == []

    txt_1 = texto_cobros_suscripcion_faltaban(1, "Netflix")
    assert txt_1 == "Se anotó 1 cobro de Netflix que faltaba."
    assert problemas_texto_web(txt_1) == []


@compiles(PGUUID, "sqlite")
def _compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture
def db_sqlite(monkeypatch):
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
        if not had_sqlite and hasattr(JSONB, "_compiler_dispatcher"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


class _SessionProxy:
    def __init__(self, s):
        self._s = s

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._s, name)


def _crear_usuario_y_billetera(db):
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
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("1000.00"),
        saldo_actual=Decimal("1000.00"),
        es_inversion=False,
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    cat = Categoria(id=uuid4(), nombre="Varios", tipo=TipoCategoria.EGRESO)
    sub = Subcategoria(id=uuid4(), categoria_id=cat.id, nombre="General", orden=1)
    db.add_all([u, b, cat, sub])
    db.commit()
    return u, b, cat, sub


def test_servicio_n3_billetera_sin_saldo(db_sqlite, monkeypatch):
    hoy = date(2026, 11, 17)
    monkeypatch.setattr("app.services.transaccion_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr("app.services.notificacion_service.hoy_argentina", lambda: hoy)
    u, b, cat, sub = _crear_usuario_y_billetera(db_sqlite)

    transaccion_service.crear_transaccion(
        db_sqlite,
        u.id,
        TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("1000.00"),
            moneda=Moneda.ARS,
            fecha=hoy,
            descripcion="Gasto total",
            categoria_id=cat.id,
            subcategoria_id=sub.id,
            billetera_id=b.id,
            metodo_pago=MetodoPago.DEBITO,
        ),
    )

    notif = db_sqlite.execute(
        select(Notificacion).where(
            Notificacion.usuario_id == u.id,
            Notificacion.tipo == TipoNotificacion.SALDO_CERO,
        )
    ).scalar_one()
    assert notif.mensaje == "Tu billetera Mercado Pago se quedó sin saldo."
    assert notif.deep_link == "/app/billeteras"
    assert problemas_texto_web(notif.mensaje) == []


def test_servicio_n7_cuota_sin_tarjeta(db_sqlite, monkeypatch):
    hoy = date(2026, 11, 17)
    monkeypatch.setattr(
        "app.services.notificacion_scheduler_service.hoy_argentina", lambda: hoy
    )
    monkeypatch.setattr(
        "app.services.notificacion_scheduler_service.intentar_tomar_lock_job",
        lambda db, name: True,
    )
    monkeypatch.setattr(
        "app.services.notificacion_scheduler_service.liberar_lock_job",
        lambda db, name: None,
    )
    monkeypatch.setattr("app.services.notificacion_service.hoy_argentina", lambda: hoy)

    u, b, cat, sub = _crear_usuario_y_billetera(db_sqlite)
    cfg = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=u.id,
        cuota_vence_anticipacion_dias=3,
        cuota_vence_web=True,
        cuota_vence_whatsapp=False,
    )
    tx = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("270003.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 9, 15),
        descripcion="Heladera",
        categoria_id=cat.id,
        subcategoria_id=sub.id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        es_padre_cuotas=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        tarjeta_id=None,
        transaccion_padre_id=tx.id,
        monto_total=Decimal("270003.00"),
        total_financiado=Decimal("270003.00"),
        moneda=Moneda.ARS,
        cantidad_cuotas=6,
        descripcion="Heladera",
    )
    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx.id,
        numero_cuota=3,
        monto_proyectado=Decimal("45000.50"),
        fecha_vencimiento=date(2026, 11, 20),
        pagada=False,
    )
    db_sqlite.add_all([cfg, tx, grupo, cuota])
    db_sqlite.commit()

    notificacion_scheduler_service._job_notificaciones_cuotas(
        lambda: _SessionProxy(db_sqlite)
    )

    notif = db_sqlite.execute(
        select(Notificacion).where(
            Notificacion.usuario_id == u.id,
            Notificacion.tipo == TipoNotificacion.CUOTA_VENCE,
        )
    ).scalar_one()
    assert notif.mensaje == "La cuota 3 de 6 de Heladera vence el 20/11: $45.000,50."
    assert notif.deep_link == "/app/tarjetas"
    assert problemas_texto_web(notif.mensaje) == []


def test_servicio_n12_resumen_ciclo(db_sqlite, monkeypatch):
    hoy = date(2026, 11, 2)
    monkeypatch.setattr(
        "app.services.notificacion_scheduler_service.hoy_argentina", lambda: hoy
    )
    monkeypatch.setattr(
        "app.services.notificacion_scheduler_service.intentar_tomar_lock_job",
        lambda db, name: True,
    )
    monkeypatch.setattr(
        "app.services.notificacion_scheduler_service.liberar_lock_job",
        lambda db, name: None,
    )
    monkeypatch.setattr("app.services.notificacion_service.hoy_argentina", lambda: hoy)
    monkeypatch.setattr(
        "app.services.dashboard_service.get_ciclo_fechas",
        lambda usuario, ref_date=None: (date(2026, 10, 2), date(2026, 11, 1)),
    )

    u, b, _, _ = _crear_usuario_y_billetera(db_sqlite)
    u.telefono = "+5491112345678"
    u.telefono_verificado = True
    cfg = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=u.id,
        resumen_ciclo_activo=True,
        resumen_ciclo_web=True,
        resumen_ciclo_whatsapp=False,
    )
    cat_ing = Categoria(id=uuid4(), nombre="Sueldo", tipo=TipoCategoria.INGRESO)
    cat_com = Categoria(id=uuid4(), nombre="Comida", tipo=TipoCategoria.EGRESO)
    db_sqlite.add_all([cfg, cat_ing, cat_com])
    db_sqlite.flush()

    tx_ing = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("500000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Sueldo",
        categoria_id=cat_ing.id,
        metodo_pago=MetodoPago.TRANSFERENCIA,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx_gas = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("420000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Supermercado",
        categoria_id=cat_com.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_sqlite.add_all([tx_ing, tx_gas])
    db_sqlite.commit()

    notificacion_scheduler_service._job_resumen_cierre_ciclo(
        lambda: _SessionProxy(db_sqlite)
    )

    notif = db_sqlite.execute(
        select(Notificacion).where(
            Notificacion.usuario_id == u.id,
            Notificacion.tipo == TipoNotificacion.RESUMEN_CICLO,
        )
    ).scalar_one()
    assert notif.mensaje == (
        "Terminó tu mes: te sobraron $80.000.\n"
        "Entraron $500.000 y gastaste $420.000.\n"
        "Lo que más pesó: Comida ($420.000)."
    )
    assert notif.deep_link is None
    assert problemas_texto_web(notif.mensaje) == []


def test_servicio_n16_resumen_impago(db_sqlite, monkeypatch):
    hoy = date(2026, 11, 10)
    monkeypatch.setattr("app.services.notificacion_service.hoy_argentina", lambda: hoy)

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
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("500000.00"),
        saldo_actual=Decimal("500000.00"),
        es_inversion=False,
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=TZ_ARGENTINA),
    )
    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        nombre="•••• 1506",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=28,
        dia_vencimiento=10,
        regla_cierre="dia_fijo",
        estado=EstadoTarjeta.ACTIVA,
    )
    cat = Categoria(id=uuid4(), nombre="Banco", tipo=TipoCategoria.EGRESO)
    sub = Subcategoria(
        id=uuid4(), categoria_id=cat.id, nombre="Tarjeta de crédito", orden=1
    )
    tx = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("120000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 15),
        descripcion="Compra",
        categoria_id=cat.id,
        subcategoria_id=sub.id,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=t.id,
        es_padre_cuotas=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u.id,
        tarjeta_id=t.id,
        transaccion_padre_id=tx.id,
        monto_total=Decimal("120000.00"),
        total_financiado=Decimal("120000.00"),
        moneda=Moneda.ARS,
        cantidad_cuotas=1,
        descripcion="Compra",
    )
    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx.id,
        numero_cuota=1,
        monto_proyectado=Decimal("120000.00"),
        fecha_vencimiento=date(2026, 11, 10),
        pagada=False,
    )
    db_sqlite.add_all([u, b, t, cat, sub, tx, grupo, cuota])
    db_sqlite.commit()

    vencimiento_tarjeta_service.procesar_vencimientos_tarjetas(db_sqlite, hoy=hoy)

    notif = db_sqlite.execute(
        select(Notificacion).where(
            Notificacion.usuario_id == u.id,
            Notificacion.tipo == TipoNotificacion.RESUMEN_IMPAGO,
        )
    ).scalar_one()
    assert notif.mensaje == (
        "El resumen de tu Visa Galicia •••• 1506 venció el 10/11: $120.000.\n"
        "¿Ya lo pagaste?"
    )
    assert notif.deep_link == f"/app/billeteras/{b.id}?tab=credito&tarjeta_id={t.id}"
    assert problemas_texto_web(notif.mensaje) == []
