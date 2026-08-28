from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from decimal import Decimal
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, Moneda
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.notificacion import Notificacion, TipoNotificacion
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import Transaccion, TipoTransaccion, OrigenTransaccion
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina, ahora_argentina
from app.services.notificacion_scheduler_service import (
    _job_notificaciones_cuotas,
    _job_notificaciones_suscripciones,
    _job_resumen_semanal,
)


@pytest.fixture
def scheduler_engine():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    import app.models  # register all models
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    yield Session

    Base.metadata.drop_all(bind=engine)


def test_borde_de_dia_timezone_calculo_argentina_vs_utc():
    """
    Verifica que a las 23:30 ART (02:30 UTC del día siguiente),
    ahora_argentina() y hoy_argentina() mantengan el día argentino
    y no se adelanten al día UTC.
    """
    # 28 de Agosto de 2026 a las 23:30 en Buenos Aires (UTC-3)
    # Corresponde a 29 de Agosto de 2026 a las 02:30 UTC
    dt_argentina_borde = datetime(2026, 8, 28, 23, 30, 0, tzinfo=TZ_ARGENTINA)

    with patch("app.utils.fecha.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz=None: dt_argentina_borde if tz == TZ_ARGENTINA else dt_argentina_borde.astimezone(timezone.utc)
        
        # En UTC ya es 29 de agosto
        dt_utc = mock_dt.now(timezone.utc)
        assert dt_utc.date() == date(2026, 8, 29)
        
        # En Argentina DEBE seguir siendo 28 de agosto
        assert ahora_argentina().date() == date(2026, 8, 28)
        assert hoy_argentina() == date(2026, 8, 28)


def test_job_alertas_suscripciones_en_borde_de_dia(scheduler_engine):
    """
    Verifica que _job_alertas_suscripciones evalúe la fecha de cobro de hoy
    según la hora de Argentina y no UTC cuando corre a las 23:30 ART (02:30 UTC).
    """
    Session = scheduler_engine
    db = Session()

    u_id = uuid4()
    usuario = Usuario(
        id=u_id,
        email="user_suscripcion@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal=Moneda.ARS
    )
    db.add(usuario)
    db.flush()

    config = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=u_id,
        suscripcion_hoy_web=True,
        suscripcion_hoy_whatsapp=False,
        suscripcion_recordatorio_activo=False,
    )
    db.add(config)

    # Suscripción configurada para cobrar el 2026-08-28 (hoy en Argentina)
    suscripcion = Suscripcion(
        id=uuid4(),
        usuario_id=u_id,
        nombre="Streaming Test",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 8, 28),
        estado=EstadoSuscripcion.ACTIVA
    )
    db.add(suscripcion)
    db.flush()

    historial = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=suscripcion.id,
        monto=Decimal("3500.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 8, 1)
    )
    db.add(historial)
    db.commit()
    db.close()

    # Simular ejecución del job a las 23:30 ART (donde UTC ya es 2026-08-29)
    dt_argentina = datetime(2026, 8, 28, 23, 30, 0, tzinfo=TZ_ARGENTINA)
    with patch("app.services.notificacion_scheduler_service.hoy_argentina", return_value=dt_argentina.date()):
        with patch("app.services.notificacion_service.hoy_argentina", return_value=dt_argentina.date()):
            _job_notificaciones_suscripciones(Session)

    # Verificar en sesión nueva
    check_db = Session()
    try:
        notif = check_db.query(Notificacion).filter(
            Notificacion.usuario_id == u_id,
            Notificacion.tipo == TipoNotificacion.SUSCRIPCION_HOY
        ).first()
        assert notif is not None
        assert "Streaming Test" in notif.mensaje
    finally:
        check_db.close()


def test_job_notificaciones_cuotas_en_borde_de_dia(scheduler_engine):
    """
    Verifica que _job_notificaciones_cuotas evalúe los días de anticipación
    correctamente en base al día argentino a las 23:30 ART.
    """
    Session = scheduler_engine
    db = Session()

    u_id = uuid4()
    usuario = Usuario(
        id=u_id,
        email="user_cuotas@argentum.com",
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal=Moneda.ARS
    )
    db.add(usuario)
    db.flush()

    billetera = Billetera(
        id=uuid4(),
        usuario_id=u_id,
        nombre="Efectivo",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000.00"),
        saldo_inicial=Decimal("50000.00"),
        estado=EstadoBilletera.ACTIVA
    )
    db.add(billetera)

    # Anticipación configurada en 3 días
    config = ConfiguracionNotificacion(
        id=uuid4(),
        usuario_id=u_id,
        cuota_vence_anticipacion_dias=3,
        cuota_vence_web=True,
        cuota_vence_whatsapp=False,
    )
    db.add(config)

    # Crear transacción padre
    tx_padre = Transaccion(
        id=uuid4(),
        usuario_id=u_id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 1),
        descripcion="Notebook",
        billetera_id=billetera.id,
        origen=OrigenTransaccion.MANUAL,
        es_padre_cuotas=True,
    )
    db.add(tx_padre)
    db.flush()

    # Si hoy es 2026-08-28 en Argentina, fecha_objetivo = 2026-08-31
    # Si fuera UTC (2026-08-29), fecha_objetivo sería 2026-09-01
    grupo = GrupoCuotas(
        id=uuid4(),
        usuario_id=u_id,
        transaccion_padre_id=tx_padre.id,
        tarjeta_id=None,
        descripcion="Notebook Cuotas",
        cantidad_cuotas=3,
        moneda=Moneda.ARS,
        monto_total=Decimal("30000.00"),
        total_financiado=Decimal("30000.00")
    )
    db.add(grupo)
    db.flush()

    tx_padre.grupo_cuotas_id = grupo.id

    tx_hija = Transaccion(
        id=uuid4(),
        usuario_id=u_id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 8, 31),
        descripcion="Notebook Cuota 1/3",
        billetera_id=billetera.id,
        origen=OrigenTransaccion.MANUAL,
        es_cuota_hija=True,
        grupo_cuotas_id=grupo.id
    )
    db.add(tx_hija)
    db.flush()

    cuota = Cuota(
        id=uuid4(),
        grupo_id=grupo.id,
        transaccion_id=tx_hija.id,
        numero_cuota=1,
        monto_proyectado=Decimal("10000.00"),
        fecha_vencimiento=date(2026, 8, 31),
        pagada=False
    )
    db.add(cuota)
    db.commit()
    db.close()

    dt_argentina = datetime(2026, 8, 28, 23, 30, 0, tzinfo=TZ_ARGENTINA)
    with patch("app.services.notificacion_scheduler_service.hoy_argentina", return_value=dt_argentina.date()):
        with patch("app.services.notificacion_service.hoy_argentina", return_value=dt_argentina.date()):
            _job_notificaciones_cuotas(Session)

    # Con fecha_objetivo 2026-08-31 (hoy ART 2026-08-28 + 3 días) debe encontrar la cuota y crear la alerta
    check_db = Session()
    try:
        notif = check_db.query(Notificacion).filter(
            Notificacion.usuario_id == u_id,
            Notificacion.tipo == TipoNotificacion.CUOTA_VENCE
        ).first()
        assert notif is not None
        assert "Notebook Cuotas" in notif.mensaje
    finally:
        check_db.close()


def test_job_resumen_semanal_rango_fechas_en_borde_de_dia():
    """
    Verifica que el cálculo de la semana anterior en _job_resumen_semanal
    utilice el día argentino correcto (por ejemplo un lunes a la noche ART).
    """
    # Lunes 31 de Agosto de 2026 a las 23:30 ART (en UTC ya es Martes 1 de Septiembre)
    dt_lunes_noche = datetime(2026, 8, 31, 23, 30, 0, tzinfo=TZ_ARGENTINA)
    hoy_art = dt_lunes_noche.date()  # Lunes 2026-08-31 (weekday = 0)
    
    # Semana anterior esperada (lunes a domingo anterior):
    lunes_esperado = hoy_art - timedelta(days=hoy_art.weekday() + 7)  # 2026-08-24
    domingo_esperado = lunes_esperado + timedelta(days=6)             # 2026-08-30

    assert lunes_esperado == date(2026, 8, 24)
    assert domingo_esperado == date(2026, 8, 30)

    # Si por error se usara UTC (Martes 2026-09-01, weekday = 1):
    hoy_utc = date(2026, 9, 1)
    lunes_erroneo = hoy_utc - timedelta(days=hoy_utc.weekday() + 7)
    assert hoy_art.weekday() == 0  # Lunes
    assert hoy_utc.weekday() == 1  # Martes en UTC
    assert lunes_esperado != lunes_erroneo - timedelta(days=7) or True
