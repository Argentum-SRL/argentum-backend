import ast
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock
import sqlite3
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import EstadoGrupoCuotas, GrupoCuotas
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import TipoMovimientoMeta
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.schemas.transaccion import TransaccionCreate, TransaccionUpdate
from app.services import (
    calendario_tarjeta_service,
    cuotas_service,
    dashboard_service,
    historial_service,
    meta_service,
    presupuesto_service,
    saldo_programado_service,
    tarjeta_service,
    transaccion_service,
)


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


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models

    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _crear_base_usuario(db_session, saldo_inicial=Decimal("1000.00")):
    usuario = Usuario(
        id=uuid4(),
        email=f"excepts_{uuid4().hex[:6]}@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db_session.add(usuario)
    db_session.flush()

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Principal",
        moneda=Moneda.ARS,
        saldo_inicial=saldo_inicial,
        saldo_actual=saldo_inicial,
        es_principal=True,
        es_efectivo=False,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    db_session.add(billetera)

    cat = Categoria(
        id=uuid4(),
        nombre="Varios",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db_session.add(cat)
    db_session.commit()
    return usuario, billetera, cat


def _tiene_warning_con_exc_info(caplog) -> bool:
    return any(
        r.levelno == logging.WARNING and r.exc_info is not None and r.exc_info[0] is not None
        for r in caplog.records
    )


def test_crear_transaccion_aviso_saldo_cero_registra_warning(db_session, monkeypatch, caplog):
    """Decisión 5.b: crear_transaccion registra WARNING con exc_info si falla el aviso de saldo cero."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("500.00"))
    monkeypatch.setattr(
        "app.services.notificacion_service.obtener_configuracion",
        MagicMock(side_effect=RuntimeError("fallo notif crear")),
    )
    caplog.set_level(logging.WARNING)
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Gasto que deja en cero",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera.id,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    transaccion_service.crear_transaccion(db_session, usuario.id, data)
    assert _tiene_warning_con_exc_info(caplog)


def test_actualizar_transaccion_aviso_saldo_cero_registra_warning(db_session, monkeypatch, caplog):
    """Decisión 5.b: actualizar_transaccion registra WARNING con exc_info si falla el aviso de saldo cero."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("1000.00"))
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("400.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Gasto inicial",
        categoria_id=cat.id,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera.id,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    tx = transaccion_service.crear_transaccion(db_session, usuario.id, data)

    monkeypatch.setattr(
        "app.services.notificacion_service.obtener_configuracion",
        MagicMock(side_effect=RuntimeError("fallo notif actualizar")),
    )
    caplog.set_level(logging.WARNING)
    transaccion_service.actualizar_transaccion(
        db_session,
        usuario.id,
        tx.id,
        TransaccionUpdate(monto=Decimal("1000.00")),
    )
    assert _tiene_warning_con_exc_info(caplog)


def test_confirmar_transaccion_ia_aviso_saldo_cero_registra_warning(db_session, monkeypatch, caplog):
    """Decisión 5.b: confirmar_transaccion_ia registra WARNING con exc_info si falla el aviso de saldo cero."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("500.00"))
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Gasto IA",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        saldo_aplicado=False,
    )
    db_session.add(tx)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notificacion_service.obtener_configuracion",
        MagicMock(side_effect=RuntimeError("fallo notif confirmar")),
    )
    caplog.set_level(logging.WARNING)
    transaccion_service.confirmar_transaccion_ia(db_session, usuario.id, tx.id, commit=False)
    assert _tiene_warning_con_exc_info(caplog)


def test_confirmar_transaccion_ia_perfil_registra_warning(db_session, monkeypatch, caplog):
    """Decisión 5.b: confirmar_transaccion_ia registra WARNING con exc_info si falla recalcular_perfil_tras_confirmacion."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("5000.00"))
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Gasto IA perfil",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
        saldo_aplicado=False,
    )
    db_session.add(tx)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.perfil_financiero_service.recalcular_perfil_tras_confirmacion",
        MagicMock(side_effect=RuntimeError("fallo recalculo perfil")),
    )
    caplog.set_level(logging.WARNING)
    transaccion_service.confirmar_transaccion_ia(db_session, usuario.id, tx.id, commit=True)
    assert _tiene_warning_con_exc_info(caplog)


def test_calcular_tasas_cuotas_invalida_registra_warning(caplog):
    """Decisión 5.b: calcular_tasas_cuotas registra WARNING con exc_info ante tasa inválida."""
    caplog.set_level(logging.WARNING)
    res = cuotas_service.calcular_tasas_cuotas("no-es-numero")
    assert res is None
    assert _tiene_warning_con_exc_info(caplog)


def test_prepagar_grupo_aviso_saldo_cero_registra_warning(db_session, monkeypatch, caplog):
    """Decisión 5.b: prepagar_grupo registra WARNING con exc_info si falla el aviso de saldo cero."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("1000.00"))
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=10,
        dia_vencimiento=20,
        limite_credito=Decimal("100000.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db_session.add(tarjeta)
    db_session.flush()

    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 1),
        descripcion="Compra cuotas",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx_padre)
    db_session.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Compra cuotas",
        monto_total=Decimal("1000.00"),
        total_financiado=Decimal("1000.00"),
        moneda=Moneda.ARS,
        cantidad_cuotas=1,
        tiene_interes=False,
        estado=EstadoGrupoCuotas.ACTIVO,
    )
    db_session.add(grupo)
    db_session.flush()

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat.id,
        grupo_cuotas_id=grupo.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 20),
        descripcion="Compra cuotas (Cuota 1/1)",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db_session.add(tx_hija)
    db_session.flush()

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        fecha_vencimiento=date(2026, 11, 20),
        monto_proyectado=Decimal("1000.00"),
        monto_real=None,
        pagada=False,
    )
    db_session.add(cuota)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notificacion_service.obtener_configuracion",
        MagicMock(side_effect=RuntimeError("fallo notif prepagar")),
    )
    caplog.set_level(logging.WARNING)
    cuotas_service.prepagar_grupo(db_session, grupo.id, usuario.id, billetera.id, cat.id)
    assert _tiene_warning_con_exc_info(caplog)


def test_prepagar_grupo_error_presupuesto_no_se_traga(db_session, monkeypatch):
    """Decisión 5.a: un error dentro de registrar_impacto_presupuesto en prepagar_grupo ya no se traga."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("5000.00"))
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=10,
        dia_vencimiento=20,
        limite_credito=Decimal("100000.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db_session.add(tarjeta)
    db_session.flush()

    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 1),
        descripcion="Compra cuotas error",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=True,
        es_cuota_hija=False,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db_session.add(tx_padre)
    db_session.flush()

    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario.id,
        tarjeta_id=tarjeta.id,
        transaccion_padre_id=tx_padre.id,
        descripcion="Compra cuotas error",
        monto_total=Decimal("1000.00"),
        total_financiado=Decimal("1000.00"),
        moneda=Moneda.ARS,
        cantidad_cuotas=1,
        tiene_interes=False,
        estado=EstadoGrupoCuotas.ACTIVO,
    )
    db_session.add(grupo)
    db_session.flush()

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        tarjeta_id=tarjeta.id,
        categoria_id=cat.id,
        grupo_cuotas_id=grupo.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("1000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 20),
        descripcion="Compra cuotas error (Cuota 1/1)",
        metodo_pago=MetodoPago.CREDITO,
        es_padre_cuotas=False,
        es_cuota_hija=True,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
    )
    db_session.add(tx_hija)
    db_session.flush()

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        fecha_vencimiento=date(2026, 11, 20),
        monto_proyectado=Decimal("1000.00"),
        monto_real=None,
        pagada=False,
    )
    db_session.add(cuota)
    db_session.commit()

    monkeypatch.setattr(
        presupuesto_service,
        "registrar_impacto_presupuesto",
        MagicMock(side_effect=RuntimeError("error en presupuesto")),
    )
    with pytest.raises(RuntimeError, match="error en presupuesto"):
        cuotas_service.prepagar_grupo(db_session, grupo.id, usuario.id, billetera.id, cat.id)


def test_registrar_movimiento_meta_alcanzada_registra_warning(db_session, monkeypatch, caplog):
    """Decisión 5.b: meta_service.registrar_movimiento registra WARNING con exc_info si falla aviso de meta alcanzada."""
    usuario, billetera, _ = _crear_base_usuario(db_session, saldo_inicial=Decimal("5000.00"))
    meta = Meta(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Fondo Vacaciones",
        monto_objetivo=Decimal("1000.00"),
        moneda=Moneda.ARS,
        monto_actual=Decimal("0.00"),
        estado=EstadoMeta.ACTIVA,
    )
    db_session.add(meta)
    db_session.commit()

    monkeypatch.setattr(
        "app.services.notificacion_service.obtener_configuracion",
        MagicMock(side_effect=RuntimeError("fallo notif meta")),
    )
    caplog.set_level(logging.WARNING)
    mov_data = MovimientoMetaCreate(
        tipo=TipoMovimientoMeta.APORTE,
        monto=Decimal("1000.00"),
        moneda_movimiento=Moneda.ARS,
        billetera_id=billetera.id,
        fecha=date(2026, 10, 10),
    )
    meta_service.registrar_movimiento(db_session, usuario.id, meta.id, mov_data)
    assert _tiene_warning_con_exc_info(caplog)


def test_obtener_resumen_impago_datos_template_invalido_registra_warning(db_session, caplog):
    """Decisión 5.b: obtener_resumen_impago registra WARNING con exc_info cuando datos_template['vencimiento'] es inválido."""
    usuario, billetera, _ = _crear_base_usuario(db_session)
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=10,
        dia_vencimiento=20,
        limite_credito=Decimal("100000.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db_session.add(tarjeta)
    db_session.flush()

    notif = Notificacion(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo=TipoNotificacion.RESUMEN_IMPAGO,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje="Resumen impago",
        entidad_tipo="tarjeta",
        entidad_id=tarjeta.id,
        datos_template={"vencimiento": "fecha-invalida"},
        archivada=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(notif)
    db_session.commit()

    caplog.set_level(logging.WARNING)
    res = tarjeta_service.obtener_resumen_impago(db_session, tarjeta)
    assert res is None
    assert _tiene_warning_con_exc_info(caplog)


def test_obtener_resumen_impago_grupo_agrupacion_invalido_registra_warning(db_session, caplog):
    """Decisión 5.b: obtener_resumen_impago registra WARNING con exc_info cuando grupo_agrupacion tiene fecha inválida."""
    usuario, billetera, _ = _crear_base_usuario(db_session)
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa",
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=10,
        dia_vencimiento=20,
        limite_credito=Decimal("100000.00"),
        estado=EstadoTarjeta.ACTIVA,
    )
    db_session.add(tarjeta)
    db_session.flush()

    notif = Notificacion(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo=TipoNotificacion.RESUMEN_IMPAGO,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje="Resumen impago",
        entidad_tipo="tarjeta",
        entidad_id=tarjeta.id,
        datos_template=None,
        grupo_agrupacion="resumen_impago_id_99999999",
        archivada=False,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(notif)
    db_session.commit()

    caplog.set_level(logging.WARNING)
    res = tarjeta_service.obtener_resumen_impago(db_session, tarjeta)
    assert res is None
    assert _tiene_warning_con_exc_info(caplog)


def test_aplicar_movimientos_con_fecha_llegada_aviso_saldo_cero_registra_warning(
    db_session, monkeypatch, caplog
):
    """Decisión 5.b: aplicar_movimientos_con_fecha_llegada registra WARNING con exc_info si falla aviso de saldo cero."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("500.00"))
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("500.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Programada",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        saldo_aplicado=False,
    )
    db_session.add(tx)
    db_session.commit()

    monkeypatch.setattr(
        saldo_programado_service,
        "obtener_configuracion",
        MagicMock(side_effect=RuntimeError("fallo notif programado")),
    )
    caplog.set_level(logging.WARNING)
    aplicados = saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db_session)
    assert aplicados == 1
    assert _tiene_warning_con_exc_info(caplog)


def test_listar_movimientos_model_validate_registra_warning(db_session, monkeypatch, caplog):
    """Decisión 5.b: listar_movimientos registra WARNING con exc_info si falla TransaccionRead.model_validate."""
    usuario, billetera, cat = _crear_base_usuario(db_session, saldo_inicial=Decimal("1000.00"))
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 10, 10),
        descripcion="Compra test",
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        saldo_aplicado=True,
    )
    db_session.add(tx)
    db_session.commit()

    monkeypatch.setattr(
        historial_service.TransaccionRead,
        "model_validate",
        MagicMock(side_effect=RuntimeError("fallo model_validate")),
    )
    caplog.set_level(logging.WARNING)
    resp = historial_service.listar_movimientos(
        db_session, usuario.id, hoy=date(2026, 10, 10)
    )
    assert len(resp.items) == 1
    assert resp.items[0].transaccion is None
    assert _tiene_warning_con_exc_info(caplog)


def test_obtener_overrides_fechas_query_falla_registra_warning(monkeypatch, caplog):
    """Decisión 5.b: _obtener_overrides_fechas registra WARNING con exc_info si falla la consulta de overrides."""
    mock_session = MagicMock()
    mock_session.query.side_effect = RuntimeError("fallo query overrides")
    monkeypatch.setattr(calendario_tarjeta_service, "object_session", lambda t: mock_session)

    tarjeta_dummy = MagicMock()
    tarjeta_dummy.id = uuid4()
    del tarjeta_dummy.fechas_resumen

    caplog.set_level(logging.WARNING)
    res = calendario_tarjeta_service._obtener_overrides_fechas(tarjeta_dummy)
    assert res == []
    assert _tiene_warning_con_exc_info(caplog)


def test_get_cotizacion_usuario_falla_registra_warning(monkeypatch, caplog):
    """Decisión 5.b: get_cotizacion_usuario registra WARNING con exc_info si falla get_cotizaciones_dolar."""
    monkeypatch.setattr(
        "app.services.dolar_service.get_cotizaciones_dolar",
        MagicMock(side_effect=RuntimeError("fallo api dolar")),
    )
    usuario_dummy = MagicMock()
    usuario_dummy.tipo_dolar = "blue"

    caplog.set_level(logging.WARNING)
    res = dashboard_service.get_cotizacion_usuario(usuario_dummy)
    assert res["error"] == "Servicio de cotizaciones no disponible"
    assert _tiene_warning_con_exc_info(caplog)


def test_e3_ast_todo_except_registrado_o_explicado():
    """
    E.3 (AST + texto): en los servicios de la decisión 5, todo except tiene
    logger.<algo>(, raise, o una línea que empieza con '# Se ignora:' dentro del bloque.
    """
    servicios = [
        "app/services/transaccion_service.py",
        "app/services/cuotas_service.py",
        "app/services/meta_service.py",
        "app/services/saldo_billetera_service.py",
        "app/services/tarjeta_service.py",
        "app/services/dashboard_service.py",
        "app/services/resumen_tarjeta_service.py",
        "app/services/saldo_programado_service.py",
        "app/services/compromisos_service.py",
        "app/services/tools_service.py",
        "app/services/historial_service.py",
        "app/services/calendario_tarjeta_service.py",
    ]
    repo_root = Path(__file__).resolve().parents[1]
    violaciones = []

    for rel_path in servicios:
        full_path = repo_root / rel_path
        src = full_path.read_text(encoding="utf-8")
        lines = src.splitlines()
        tree = ast.parse(src, filename=rel_path)

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                tiene_logger = False
                tiene_raise = False
                for child in ast.walk(node):
                    if isinstance(child, ast.Raise):
                        tiene_raise = True
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        if isinstance(child.func.value, ast.Name) and child.func.value.id == "logger":
                            tiene_logger = True
                start_l = node.lineno
                end_l = getattr(node, "end_lineno", start_l + 5)
                block_lines = lines[start_l - 1 : end_l]
                tiene_se_ignora = any(
                    ln.strip().startswith("# Se ignora:") for ln in block_lines
                )
                if not (tiene_logger or tiene_raise or tiene_se_ignora):
                    violaciones.append(f"{rel_path}:{start_l}")

    assert violaciones == [], f"Excepts sin logger, raise ni '# Se ignora:': {violaciones}"
